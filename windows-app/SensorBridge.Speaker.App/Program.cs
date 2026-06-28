using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace SensorBridge.Speaker.App
{
    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm(AppOptions.Parse(args)));
        }
    }

    internal sealed class AppOptions
    {
        public string ProjectRoot = ResolveDefaultProjectRoot();
        public string BaseUrl = "http://192.168.0.24:27180";
        public string CaptureDevice = "CABLE Output";
        public int DurationSeconds = 5;

        public static AppOptions Parse(string[] args)
        {
            AppOptions options = new AppOptions();
            for (int i = 0; i < args.Length; i++)
            {
                string value = i + 1 < args.Length ? args[i + 1] : "";
                if (args[i] == "--project-root" && value.Length > 0) { options.ProjectRoot = Path.GetFullPath(value); i++; }
                else if (args[i] == "--base-url" && value.Length > 0) { options.BaseUrl = value; i++; }
                else if (args[i] == "--capture-device" && value.Length > 0) { options.CaptureDevice = value; i++; }
                else if (args[i] == "--duration-seconds" && value.Length > 0)
                {
                    int parsed;
                    if (Int32.TryParse(value, out parsed)) { options.DurationSeconds = Math.Max(1, parsed); }
                    i++;
                }
            }
            return options;
        }

        private static string ResolveDefaultProjectRoot()
        {
            string current = AppDomain.CurrentDomain.BaseDirectory;
            for (int depth = 0; depth < 8 && !String.IsNullOrEmpty(current); depth++)
            {
                if (File.Exists(Path.Combine(current, "speaker_bridge.py")) &&
                    Directory.Exists(Path.Combine(current, "speakerclient")))
                {
                    return Path.GetFullPath(current);
                }
                DirectoryInfo parent = Directory.GetParent(current);
                if (parent == null) { break; }
                current = parent.FullName;
            }
            return Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "..", ".."));
        }
    }

    internal sealed class MainForm : Form
    {
        private readonly AppOptions _options;
        private readonly JavaScriptSerializer _json = new JavaScriptSerializer();
        private TextBox _baseUrlText;
        private TextBox _captureText;
        private Button _startButton;
        private Button _stopButton;
        private Button _refreshButton;
        private Button _testButton;
        private Button _webrtcButton;
        private Label _serviceValue;
        private Label _routeValue;
        private Label _ipadValue;
        private Label _volumeValue;
        private Label _chunksValue;
        private Label _warningValue;
        private TextBox _details;
        private NotifyIcon _trayIcon;
        private ContextMenuStrip _trayMenu;
        private Process _streamProcess;
        private bool _allowExit;

        public MainForm(AppOptions options)
        {
            _options = options;
            Text = "SensorBridge Speaker";
            MinimumSize = new Size(880, 560);
            Size = new Size(980, 650);
            StartPosition = FormStartPosition.CenterScreen;
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            BackColor = Color.FromArgb(246, 247, 249);
            Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            _json.MaxJsonLength = Int32.MaxValue;
            BuildLayout();
            BuildTrayIcon();
            RunStatus();
        }

        private void BuildLayout()
        {
            TableLayoutPanel root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.RowCount = 4;
            root.ColumnCount = 1;
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 74));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 58));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 150));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            Controls.Add(root);

            Panel header = new Panel();
            header.Dock = DockStyle.Fill;
            header.BackColor = Color.FromArgb(77, 83, 42);
            header.Padding = new Padding(22, 13, 22, 8);
            root.Controls.Add(header, 0, 0);

            Label title = new Label();
            title.Text = "SensorBridge Speaker";
            title.ForeColor = Color.White;
            title.Font = new Font("Segoe UI Semibold", 19F, FontStyle.Bold, GraphicsUnit.Point);
            title.AutoSize = true;
            title.Location = new Point(0, 4);
            header.Controls.Add(title);

            Label subtitle = new Label();
            subtitle.Text = "HTTP diagnostic bridge; WebRTC/Opus is the production speaker path";
            subtitle.ForeColor = Color.FromArgb(244, 242, 214);
            subtitle.AutoSize = true;
            subtitle.Location = new Point(2, 43);
            header.Controls.Add(subtitle);

            Panel toolbar = new Panel();
            toolbar.Dock = DockStyle.Fill;
            toolbar.BackColor = Color.White;
            toolbar.Padding = new Padding(20, 12, 20, 10);
            root.Controls.Add(toolbar, 0, 1);

            Label urlLabel = new Label();
            urlLabel.Text = "Base URL";
            urlLabel.AutoSize = true;
            urlLabel.Location = new Point(0, 11);
            toolbar.Controls.Add(urlLabel);

            _baseUrlText = new TextBox();
            _baseUrlText.Text = _options.BaseUrl;
            _baseUrlText.Width = 250;
            _baseUrlText.Location = new Point(72, 8);
            toolbar.Controls.Add(_baseUrlText);

            Label captureLabel = new Label();
            captureLabel.Text = "Capture";
            captureLabel.AutoSize = true;
            captureLabel.Location = new Point(334, 11);
            toolbar.Controls.Add(captureLabel);

            _captureText = new TextBox();
            _captureText.Text = _options.CaptureDevice;
            _captureText.Width = 96;
            _captureText.Location = new Point(390, 8);
            toolbar.Controls.Add(_captureText);

            _startButton = AddToolbarButton(toolbar, "Start", 500, delegate { StartStream(); });
            _stopButton = AddToolbarButton(toolbar, "Stop", 578, delegate { StopStream(); });
            _refreshButton = AddToolbarButton(toolbar, "Refresh", 656, delegate { RunStatus(); });
            _testButton = AddToolbarButton(toolbar, "HTTP Test", 746, delegate { RunRouteTest(); });
            _webrtcButton = AddToolbarButton(toolbar, "WebRTC", 842, delegate { RunWebRTCTest(); });

            TableLayoutPanel statusGrid = new TableLayoutPanel();
            statusGrid.Dock = DockStyle.Fill;
            statusGrid.Padding = new Padding(18, 10, 18, 10);
            statusGrid.ColumnCount = 3;
            statusGrid.RowCount = 2;
            for (int i = 0; i < 3; i++) { statusGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 33.333F)); }
            statusGrid.RowStyles.Add(new RowStyle(SizeType.Percent, 50));
            statusGrid.RowStyles.Add(new RowStyle(SizeType.Percent, 50));
            root.Controls.Add(statusGrid, 0, 2);

            _serviceValue = AddCard(statusGrid, 0, 0, "Service");
            _routeValue = AddCard(statusGrid, 1, 0, "VB-CABLE");
            _ipadValue = AddCard(statusGrid, 2, 0, "iPad");
            _volumeValue = AddCard(statusGrid, 0, 1, "Windows outbound");
            _chunksValue = AddCard(statusGrid, 1, 1, "iPad downlink");
            _warningValue = AddCard(statusGrid, 2, 1, "Transport note");

            _details = new TextBox();
            _details.Dock = DockStyle.Fill;
            _details.Multiline = true;
            _details.ReadOnly = true;
            _details.ScrollBars = ScrollBars.Both;
            _details.Font = new Font("Consolas", 9F, FontStyle.Regular, GraphicsUnit.Point);
            _details.BackColor = Color.White;
            root.Controls.Add(_details, 0, 3);

            RenderIdle();
        }

        private static Button AddToolbarButton(Control parent, string text, int x, EventHandler click)
        {
            Button button = new Button();
            button.Text = text;
            button.Width = text.Length > 7 ? 92 : 74;
            button.Height = 28;
            button.Location = new Point(x, 6);
            button.Click += click;
            parent.Controls.Add(button);
            return button;
        }

        private void BuildTrayIcon()
        {
            _trayMenu = new ContextMenuStrip();
            _trayMenu.Items.Add("Open", null, delegate { RestoreFromTray(); });
            _trayMenu.Items.Add("Start", null, delegate { RestoreFromTray(); StartStream(); });
            _trayMenu.Items.Add("Stop", null, delegate { StopStream(); });
            _trayMenu.Items.Add("HTTP Test", null, delegate { RestoreFromTray(); RunRouteTest(); });
            _trayMenu.Items.Add("WebRTC Test", null, delegate { RestoreFromTray(); RunWebRTCTest(); });
            _trayMenu.Items.Add(new ToolStripSeparator());
            _trayMenu.Items.Add("Exit", null, delegate { _allowExit = true; Close(); });

            _trayIcon = new NotifyIcon();
            _trayIcon.Text = "SensorBridge Speaker";
            _trayIcon.Icon = Icon == null ? SystemIcons.Application : Icon;
            _trayIcon.ContextMenuStrip = _trayMenu;
            _trayIcon.Visible = true;
            _trayIcon.DoubleClick += delegate { RestoreFromTray(); };
        }

        private static Label AddCard(TableLayoutPanel grid, int column, int row, string title)
        {
            Panel card = new Panel();
            card.Dock = DockStyle.Fill;
            card.Margin = new Padding(5);
            card.BackColor = Color.White;
            grid.Controls.Add(card, column, row);

            Panel accent = new Panel();
            accent.Dock = DockStyle.Left;
            accent.Width = 4;
            accent.BackColor = Color.FromArgb(142, 151, 70);
            card.Controls.Add(accent);

            Label titleLabel = new Label();
            titleLabel.Text = title;
            titleLabel.Location = new Point(16, 8);
            titleLabel.Size = new Size(260, 16);
            titleLabel.ForeColor = Color.FromArgb(92, 104, 116);
            titleLabel.Font = new Font("Segoe UI Semibold", 8.5F, FontStyle.Bold, GraphicsUnit.Point);
            titleLabel.AutoEllipsis = true;
            card.Controls.Add(titleLabel);

            Label value = new Label();
            value.Text = "-";
            value.Location = new Point(16, 29);
            value.Size = new Size(260, 20);
            value.ForeColor = Color.FromArgb(28, 39, 50);
            value.AutoEllipsis = true;
            card.Controls.Add(value);
            card.Resize += delegate
            {
                int width = Math.Max(60, card.ClientSize.Width - 28);
                titleLabel.Width = width;
                value.Width = width;
            };
            return value;
        }

        private void RunStatus()
        {
            RunBridgeInBackground("status", "checking...");
        }

        private void RunRouteTest()
        {
            RunBridgeInBackground("route-test", "testing route...");
        }

        private void RunWebRTCTest()
        {
            RunBridgeInBackground("webrtc-speaker", "testing WebRTC...");
        }

        private void RunBridgeInBackground(string command, string label)
        {
            SetButtons(false);
            _serviceValue.Text = label;
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    Dictionary<string, object> payload = RunBridgeCommand(command, true);
                    Ui(delegate { RenderStatus(payload); });
                }
                catch (Exception exc)
                {
                    Ui(delegate
                    {
                        _serviceValue.Text = "failed";
                        _details.Text = exc.ToString();
                    });
                }
                finally
                {
                    Ui(delegate { SetButtons(true); });
                }
            });
        }

        private void StartStream()
        {
            if (_streamProcess != null && !_streamProcess.HasExited)
            {
                _serviceValue.Text = "webrtc streaming";
                return;
            }

            string bridge = BridgePath();
            string python = ResolvePython();
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = python == "py" ? "py" : python;
            info.Arguments = BuildArguments(python, bridge, "webrtc-speaker", "0");
            info.WorkingDirectory = _options.ProjectRoot;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = false;
            info.RedirectStandardError = false;
            _streamProcess = Process.Start(info);
            _serviceValue.Text = "webrtc streaming";
            _details.Text = "Streaming CABLE Output through WebRTC/Opus until Stop is pressed. HTTP chunk remains diagnostic only.";
        }

        private void StopStream()
        {
            if (_streamProcess != null && !_streamProcess.HasExited)
            {
                _streamProcess.Kill();
                _streamProcess.WaitForExit(3000);
            }
            _streamProcess = null;
            if (!IsDisposed && _serviceValue != null && !_serviceValue.IsDisposed) { _serviceValue.Text = "stopped"; }
        }

        private Dictionary<string, object> RunBridgeCommand(string command, bool captureOutput)
        {
            string bridge = BridgePath();
            string python = ResolvePython();
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = python == "py" ? "py" : python;
            info.Arguments = BuildArguments(python, bridge, command);
            info.WorkingDirectory = _options.ProjectRoot;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = captureOutput;
            info.RedirectStandardError = captureOutput;

            using (Process process = Process.Start(info))
            {
                string output = captureOutput ? process.StandardOutput.ReadToEnd() : "";
                string error = captureOutput ? process.StandardError.ReadToEnd() : "";
                process.WaitForExit();
                if (String.IsNullOrWhiteSpace(output)) { throw new InvalidOperationException(error.Length > 0 ? error : "speaker_bridge.py returned no output"); }
                Dictionary<string, object> payload = _json.Deserialize<Dictionary<string, object>>(ExtractJson(output));
                if (process.ExitCode != 0 && payload == null) { throw new InvalidOperationException(error.Length > 0 ? error : output); }
                return payload;
            }
        }

        private string BuildArguments(string python, string bridge, string command)
        {
            return BuildArguments(python, bridge, command, Convert.ToString(_options.DurationSeconds));
        }

        private string BuildArguments(string python, string bridge, string command, string durationSeconds)
        {
            return (python == "py" ? "-3 " : "") +
                Quote(bridge) +
                " --base-url " + Quote(_baseUrlText.Text) +
                " --capture-device " + Quote(_captureText.Text) +
                " --duration-seconds " + durationSeconds +
                " " + command;
        }

        private string BridgePath()
        {
            string bridge = Path.Combine(_options.ProjectRoot, "speaker_bridge.py");
            if (!File.Exists(bridge)) { throw new FileNotFoundException("speaker_bridge.py not found", bridge); }
            return bridge;
        }

        private void RenderIdle()
        {
            _serviceValue.Text = "not started";
            _routeValue.Text = "-";
            _ipadValue.Text = "-";
            _volumeValue.Text = "-";
            _chunksValue.Text = "-";
            _warningValue.Text = "HTTP diagnostic; WebRTC/Opus planned";
            _details.Text = "Set Windows/app playback to CABLE Input. Start uses the production WebRTC/Opus downlink; HTTP Test is diagnostic only.";
        }

        private void RenderStatus(Dictionary<string, object> payload)
        {
            bool ok = Bool(payload, "ok");
            string command = Value(payload, "command");
            bool isWebRtc = Value(payload, "transport") == "webrtc_opus_downlink" || command == "webrtc_speaker";
            _serviceValue.Text = ok ? (isWebRtc ? "webrtc ok" : "ready") : (isWebRtc ? "webrtc pending" : "not ready");
            _routeValue.Text = Bool(payload, "capture_device_found") ? Value(payload, "capture_device") : "CABLE Output missing";
            if (isWebRtc)
            {
                _ipadValue.Text = Join("state ", Nested(payload, "ipad_inbound", "speakerDownlinkState"));
                _volumeValue.Text = Join(Nested(payload, "windows_outbound", "packets_sent"), " packets / ", Nested(payload, "windows_outbound", "bytes_sent"), " bytes");
                _chunksValue.Text = Join(Nested(payload, "ipad_inbound", "speakerDownlinkPacketsReceived"), " packets / ", Nested(payload, "ipad_inbound", "speakerDownlinkBytesReceived"), " bytes");
                _warningValue.Text = BoolNested(payload, "ipad_inbound", "speakerDownlinkStatsFresh") ? "WebRTC/Opus production" : "waiting for iPad downlink stats";
            }
            else
            {
                _ipadValue.Text = Bool(payload, "ipad_playback_scheduled") ? "playback scheduled" : "not scheduled";
                _volumeValue.Text = Join("peak ", Value(payload, "peak_abs"), " RMS ", Value(payload, "rms"));
                _chunksValue.Text = Join(Value(payload, "chunks_sent"), " chunks");
                _warningValue.Text = "HTTP diagnostic; watch droppedChunks";
            }
            _details.Text = _json.Serialize(payload);
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            if (WindowState == FormWindowState.Minimized) { HideToTray(); }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!_allowExit && e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                HideToTray();
                return;
            }
            base.OnFormClosing(e);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                StopStream();
                if (_trayIcon != null) { _trayIcon.Visible = false; _trayIcon.Dispose(); _trayIcon = null; }
                if (_trayMenu != null) { _trayMenu.Dispose(); _trayMenu = null; }
            }
            base.Dispose(disposing);
        }

        private void HideToTray()
        {
            Hide();
            ShowInTaskbar = false;
            if (_trayIcon != null) { _trayIcon.Visible = true; }
        }

        private void RestoreFromTray()
        {
            ShowInTaskbar = true;
            Show();
            WindowState = FormWindowState.Normal;
            Activate();
        }

        private void SetButtons(bool enabled)
        {
            _startButton.Enabled = enabled;
            _refreshButton.Enabled = enabled;
            _testButton.Enabled = enabled;
            _webrtcButton.Enabled = enabled;
        }

        private void Ui(MethodInvoker action)
        {
            if (IsDisposed) { return; }
            if (InvokeRequired) { BeginInvoke(action); }
            else { action(); }
        }

        private static string Value(Dictionary<string, object> root, string key)
        {
            object value;
            return root != null && root.TryGetValue(key, out value) && value != null ? Convert.ToString(value) : "";
        }

        private static bool Bool(Dictionary<string, object> root, string key)
        {
            object value;
            if (root == null || !root.TryGetValue(key, out value) || value == null) { return false; }
            if (value is bool) { return (bool)value; }
            bool parsed;
            return Boolean.TryParse(Convert.ToString(value), out parsed) && parsed;
        }

        private static string Nested(Dictionary<string, object> root, string parentKey, string key)
        {
            object parent;
            if (root == null || !root.TryGetValue(parentKey, out parent) || parent == null) { return ""; }
            Dictionary<string, object> dictionary = parent as Dictionary<string, object>;
            if (dictionary == null) { return ""; }
            return Value(dictionary, key);
        }

        private static bool BoolNested(Dictionary<string, object> root, string parentKey, string key)
        {
            object parent;
            if (root == null || !root.TryGetValue(parentKey, out parent) || parent == null) { return false; }
            Dictionary<string, object> dictionary = parent as Dictionary<string, object>;
            return dictionary != null && Bool(dictionary, key);
        }

        private static string Join(params string[] parts)
        {
            StringBuilder builder = new StringBuilder();
            foreach (string part in parts)
            {
                if (!String.IsNullOrWhiteSpace(part)) { builder.Append(part); }
            }
            return builder.Length == 0 ? "-" : builder.ToString();
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static string ResolvePython()
        {
            if (CommandWorks("py", "-3 --version")) { return "py"; }
            if (CommandWorks("python", "--version")) { return "python"; }
            throw new InvalidOperationException("No usable Python launcher was found.");
        }

        private static bool CommandWorks(string fileName, string arguments)
        {
            try
            {
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = fileName;
                info.Arguments = arguments;
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                using (Process process = Process.Start(info))
                {
                    process.WaitForExit(3000);
                    return process.ExitCode == 0;
                }
            }
            catch { return false; }
        }

        private static string ExtractJson(string output)
        {
            int start = output.IndexOf('{');
            int end = output.LastIndexOf('}');
            if (start < 0 || end <= start) { throw new InvalidOperationException("No JSON object found in speaker_bridge.py output."); }
            return output.Substring(start, end - start + 1);
        }
    }
}
