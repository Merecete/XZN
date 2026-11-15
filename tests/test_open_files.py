"""Opening files from the file manager: play at once, one XZN only,
and hiding the playlist keeps the window width."""
import os, sys, time, tempfile
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn

ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)

d = tempfile.mkdtemp()
a, b, c = (os.path.join(d, n) for n in ("a.mp3", "b.flac", "c.mkv"))
for p in (a, b, c):
    open(p, "wb").close()

# ---------------------------------------------------------------- single instance
port = 40000 + os.getpid() % 9000
with mock.patch.object(xzn, "_instance_port", return_value=port):
    srv = xzn.InstanceServer.start()
    check(isinstance(srv, xzn.InstanceServer), "first XZN listens for later starts")
    check(xzn.InstanceServer.start() is False, "second XZN sees that one is running")
    check(xzn.send_to_running_instance([b, c]), "second XZN hands its files over")
    time.sleep(0.1)
    check(srv.take() == [b, c] and srv.take() is None, "running XZN receives the files once")
    check(xzn.send_to_running_instance([]) and srv.take() == [], "plain second start just brings XZN to the front")
    with open(xzn._instance_key_path(), "w") as f:
        f.write("wrong")
    check(not xzn.send_to_running_instance([a], wait=0.3) and srv.take() is None,
          "a message without the right key is ignored")
with mock.patch.object(xzn, "_instance_port", return_value=port + 1):
    t0 = time.monotonic()
    check(not xzn.send_to_running_instance([a], wait=0.3) and time.monotonic() - t0 < 2,
          "no running XZN: start normally")

# ---------------------------------------------------------------- open = play
xzn.ensure_sample_track = lambda: None
with mock.patch.object(xzn.Player, "start"):
    app = xzn.App()
app.playlist[:] = [a]
with mock.patch.object(app, "play_index") as pi, mock.patch.object(app, "_probe_durations_async"):
    app.open_files([b])
    check(app.playlist == [a, b] and pi.call_args.args == (1,), "opened file is added and played at once")
    app.open_files([a])
    check(app.playlist == [a, b] and pi.call_args.args == (0,), "file already in the playlist: played, not added twice")
    app.open_files([os.path.join(d, "nope.mp3"), os.path.join(d, "x.txt")])
    check(pi.call_count == 2, "missing or non-media files are ignored")
    srv2 = mock.MagicMock()
    app.attach_instance_server(srv2)
    srv2.take.return_value = [c]
    app._last_remote_open = 0.0
    with mock.patch.object(app, "_bring_to_front") as front:
        app._poll_instance_server()
        check(front.called and pi.call_args.args == (2,), "file from a second start is played, window comes forward")
        srv2.take.return_value = [os.path.join(d, "a.mp3")]
        with mock.patch.object(app, "add_paths") as ap:
            app._poll_instance_server()
            check(ap.called and pi.call_count == 3, "several files opened at once: first plays, the rest are added")

# ---------------------------------------------------------------- hide playlist keeps the width
app._mini = False
app._pl_hidden = False
app.root.winfo_width.return_value = 610
app.root.winfo_height.return_value = 800
app._main_frame.winfo_reqwidth.return_value = 660     # wider than the window
app._main_frame.winfo_reqheight.return_value = 380
app.toggle_playlist()
check(app.root.geometry.call_args.args == ("610x380",) and app.root.minsize.call_args.args[0] == 610,
      f"hiding the playlist keeps the width {app.root.geometry.call_args}")
app._main_frame.winfo_reqheight.return_value = 600
app.toggle_playlist()
check(app.root.geometry.call_args.args == ("610x800",), f"showing it again keeps the width {app.root.geometry.call_args}")
print("ALL PASS" if ok else "SOME FAILED")
