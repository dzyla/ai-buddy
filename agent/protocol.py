"""Front-end IPC: newline-delimited JSON over fd 3 (a socketpair end the C front-end created).

Engine -> front-end ("ev"):
  config   {interactive, quiet, hide_details, permission_mode, model, session_id}   once, first
  text     {s}            streamed assistant content chunk (front-end prints it dim when stdout is a tty)
  thinking {s, stream}    stream=true: reasoning_content chunk; false: a `think` tool box
  status   {s, ...}       "request" (spinner on) / "toolcall" / "response" (spinner off),
                          "agent_start" / "agent_end", "mode" {mode}
  tool_start {name, args} / tool_end {name, ok, status, preview, elapsed}
  approve  {id, kind, ...} kind: "command" {command} (y/n/b), "confirm" {title, detail},
                          "continue" {steps}; the front-end answers approve_reply
  final    {markdown, rendered, model, turn, tools, elapsed, tps}   the answer box
  out {s} / err {s}       raw text for the front-end's stdout / stderr (keeps one writer per stream)
  info     {title, body}  info box (stdout)
  read     {ctx_pct}      interactive: ask for the next line
  done     {code}         last event; the front-end exits with code
Front-end -> engine:
  input {s} · command {s} (colon-commands the front-end does not handle itself) · interrupt (ESC)
  set {key: "permission_mode"|"auto_approve"|"hide_details", value} · btw {s} · eof
  approve_reply {id, ok, answer, mode?}
"""
import json
import os
import queue
import socket
import sys
import threading


class Channel:
    """The engine's end of the front-end socket. A reader thread routes approve replies to the
    waiting approve() call, interrupt/set/btw to callbacks (they act mid-task), and
    input/command/eof to next_input()."""

    def __init__(self, fd):
        self.sock = socket.socket(fileno=fd)
        try:
            os.set_inheritable(self.sock.fileno(), False)
        except OSError:
            pass
        self._wlock = threading.Lock()
        self._inputs = queue.Queue()
        self._replies = {}
        self._rcond = threading.Condition()
        self._next_id = 0
        self._interrupt_cbs = []
        self._set_cbs = []
        self.interrupted = threading.Event()
        self.closed = threading.Event()
        self.btw = queue.Queue()
        self.headless = False
        self._reader = threading.Thread(target=self._read_loop, name="frontend-reader", daemon=True)
        self._reader.start()

    # -- outgoing --
    def send(self, ev, **fields):
        msg = {"ev": ev}
        msg.update(fields)
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8", "replace")
        with self._wlock:
            try:
                self.sock.sendall(data)
            except OSError:
                self.closed.set()

    def approve(self, kind, **fields):
        """Ask the front-end; blocks for its reply. A dead front-end answers {"ok": False, "answer": "eof"}."""
        with self._rcond:
            self._next_id += 1
            rid = self._next_id
        self.send("approve", id=rid, kind=kind, **fields)
        with self._rcond:
            while rid not in self._replies and not self.closed.is_set():
                self._rcond.wait(0.5)
            return self._replies.pop(rid, {"ok": False, "answer": "eof"})

    # -- incoming --
    def on_interrupt(self, cb):
        self._interrupt_cbs.append(cb)

    def on_set(self, cb):
        self._set_cbs.append(cb)

    def next_input(self, timeout=None):
        """The next input/command/set/eof event (blocking)."""
        try:
            return self._inputs.get(timeout=timeout)
        except queue.Empty:
            return None

    def _read_loop(self):
        f = self.sock.makefile("rb")
        try:
            for raw in f:
                try:
                    msg = json.loads(raw.decode("utf-8", "surrogateescape"))
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                ev = msg.get("ev")
                if ev == "approve_reply":
                    with self._rcond:
                        self._replies[msg.get("id")] = msg
                        self._rcond.notify_all()
                elif ev == "interrupt":
                    self.interrupted.set()
                    for cb in list(self._interrupt_cbs):
                        try:
                            cb()
                        except Exception:
                            pass
                elif ev == "btw":
                    self.btw.put(msg.get("s") or "")
                elif ev == "set":
                    for cb in list(self._set_cbs):
                        try:
                            cb(msg.get("key"), msg.get("value"))
                        except Exception:
                            pass
                    self._inputs.put(msg)
                else:
                    self._inputs.put(msg)
        except OSError:
            pass
        finally:
            self.closed.set()
            with self._rcond:
                self._rcond.notify_all()
            self._inputs.put({"ev": "eof"})

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Local:
    """No front-end (`python3 -m agent "prompt"`): print what the C front-end would for a
    non-tty stdout — answers to stdout, diagnostics to stderr — and refuse approvals (no tty)."""

    headless = True

    def __init__(self):
        self.interrupted = threading.Event()
        self.closed = threading.Event()
        self.btw = queue.Queue()

    def send(self, ev, **f):
        out = sys.__stdout__  # the engine points sys.stdout at stderr (stray prints)
        if ev == "final":
            text = f.get("rendered") or f.get("markdown") or ""
            if text:
                out.write(text + "\n")
                out.flush()
        elif ev == "out":
            out.write(f.get("s", ""))
            out.flush()
        elif ev == "info":
            out.write("\n\033[1;36mℹ %s\033[0m\n" % (f.get("title") or "Info"))
            if f.get("body"):
                out.write("\033[2m  %s\033[0m\n" % f["body"])
            out.write("\n")
            out.flush()
        elif ev == "err":
            sys.stderr.write(f.get("s", ""))
            sys.stderr.flush()

    def approve(self, kind, **f):
        return {"ok": False, "answer": "notty"}

    def on_interrupt(self, cb):
        pass

    def on_set(self, cb):
        pass

    def next_input(self, timeout=None):
        return {"ev": "eof"}

    def close(self):
        pass
