# 3270 TN3270 Server Stub

This project implements a basic TN3270 (Telnet 3270) server in Python. It is designed to demonstrate the fundamentals of the TN3270 protocol and provide a starting point for building mainframe-style terminal applications.

## Features
- Listens for incoming TN3270 connections on port 23 (configurable)
- Performs Telnet option negotiation for TN3270 compatibility
- Sends a simple IBM 3270-style logon panel to clients
- Parses user input from 3270 fields (USERID and PASSWORD)
- Handles timeouts and basic session management

## How It Works
1. The server starts and listens for incoming connections.
2. When a client connects, the server negotiates Telnet options required for TN3270.
3. The server sends a logon panel with input fields for USERID and PASSWORD.
4. The client fills in the fields and submits the data.
5. The server parses the input and prints the results to the console.

## Limitations
- This is a stub implementation and does not fully support the 3270 data stream protocol.
- Only basic field parsing and input handling are implemented.
- No authentication or backend integration is provided.
- Not suitable for production use or real mainframe emulation.

## Requirements
- Python 3.8+
- Run as administrator/root if binding to port 23 (or use a higher port)

## Usage
```sh
python server.py
```

Connect using a TN3270 emulator (e.g., x3270, wc3270) to `localhost:23`.

## Extending
To build a full TN3270 server, you will need to:
- Implement full 3270 data stream parsing and generation
- Manage screen buffers and field attributes
- Integrate with backend applications or authentication systems
- Handle multiple sessions and advanced Telnet options

## References
- [TN3270 Protocol Specification](https://tools.ietf.org/html/rfc2355)
- [x3270 Emulator](https://x3270.miraheze.org/wiki/Main_Page)
- [IBM 3270 Data Stream Reference](https://www.ibm.com/docs/en/zos/2.4.0?topic=streams-3270-data-stream)

---
This project is for educational and prototyping purposes only.
