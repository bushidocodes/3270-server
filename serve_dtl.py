"""Run the TN3270 server with screens rendered from the DTL panel sources.

This reuses all of server.py (negotiation, the login/ISPF session loop, field
parsing) but swaps the two hand-built screen functions for DTL-backed ones —
so connecting an emulator exercises the real dtl.py -> screen.py -> 3270 path.
server.py itself is left untouched; we just rebind its two module globals.

    python serve_dtl.py
    # then connect a TN3270 emulator to localhost:2323
"""
from datetime import datetime

import server
from screen import Text, DisplayIntensity
from dtl import load_panel


def send_tso_logon(client_socket, error_msg: str = None):
    screen = load_panel("logon")
    if error_msg:
        col = max(0, (80 - len(error_msg)) // 2)
        screen.add(Text(19, col, error_msg, DisplayIntensity.HIGH))
    client_socket.sendall(screen.render())


def send_ispf_menu(client_socket, userid: str, short_msg: str = None):
    screen = load_panel("ispf", userid=userid.ljust(8), time=datetime.now().strftime("%H:%M"))
    if short_msg:
        screen.add(Text(2, 25, short_msg[:54], DisplayIntensity.HIGH))
    client_socket.sendall(screen.render())


# Rebind the globals that handle_client() calls.
server.send_tso_logon = send_tso_logon
server.send_ispf_menu = send_ispf_menu


if __name__ == "__main__":
    print("DTL-backed TN3270 server: panels rendered from panels/*.dtl")
    server.run_tn3270_server()
