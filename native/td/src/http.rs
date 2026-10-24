//! One keep-alive HTTP/1.1 connection to the loopback Daemon.
//!
//! An idle connection the Daemon already closed is replaced before sending, so only a
//! failure to open a connection is reported as `Failure::Connect`.

use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::{Duration, Instant};

pub enum Failure {
    /// Nothing was sent.
    Connect,
    /// The exchange broke after the connection opened; the request may have been received.
    Exchange,
}

/// A socket whose every read and write only gets the time left in the current exchange.
struct Socket {
    stream: TcpStream,
    end: Instant,
}

impl Socket {
    fn remaining(&self) -> io::Result<Duration> {
        let left = self.end.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "the exchange ran out of time",
            ));
        }
        Ok(left)
    }
}

impl Read for Socket {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        self.stream.set_read_timeout(Some(self.remaining()?))?;
        self.stream.read(buffer)
    }
}

impl Write for Socket {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        self.stream.set_write_timeout(Some(self.remaining()?))?;
        self.stream.write(buffer)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.stream.flush()
    }
}

pub struct Connection {
    address: SocketAddr,
    idle: Option<BufReader<Socket>>,
}

impl Connection {
    pub fn new(address: SocketAddr) -> Self {
        Self {
            address,
            idle: None,
        }
    }

    pub fn exchange(
        &mut self,
        method: &str,
        path: &str,
        headers: &[(&str, String)],
        body: Option<&[u8]>,
        timeout: Duration,
    ) -> Result<(u16, Vec<u8>), Failure> {
        let end = Instant::now() + timeout;
        let mut stream = match self.reusable() {
            Some(stream) => stream,
            None => {
                let stream = TcpStream::connect_timeout(&self.address, timeout)
                    .map_err(|_| Failure::Connect)?;
                stream.set_nodelay(true).map_err(|_| Failure::Connect)?;
                BufReader::new(Socket { stream, end })
            }
        };
        stream.get_mut().end = end;
        let (status, content, keep_alive) =
            send(&mut stream, &self.address, method, path, headers, body)
                .map_err(|_| Failure::Exchange)?;
        if keep_alive {
            self.idle = Some(stream);
        }
        Ok((status, content))
    }

    fn reusable(&mut self) -> Option<BufReader<Socket>> {
        let stream = self.idle.take()?;
        // An idle connection only becomes readable when the Daemon closed it.
        let socket = &stream.get_ref().stream;
        socket.set_nonblocking(true).ok()?;
        let mut probe = [0u8; 1];
        let closed = !matches!(socket.peek(&mut probe), Err(error) if error.kind() == io::ErrorKind::WouldBlock);
        socket.set_nonblocking(false).ok()?;
        (!closed && stream.buffer().is_empty()).then_some(stream)
    }
}

fn send(
    stream: &mut BufReader<Socket>,
    address: &SocketAddr,
    method: &str,
    path: &str,
    headers: &[(&str, String)],
    body: Option<&[u8]>,
) -> io::Result<(u16, Vec<u8>, bool)> {
    let mut request =
        format!("{method} {path} HTTP/1.1\r\nHost: {address}\r\nAccept-Encoding: identity\r\n");
    for (name, value) in headers {
        request.push_str(&format!("{name}: {value}\r\n"));
    }
    if let Some(body) = body {
        request.push_str(&format!(
            "Content-Type: application/json\r\nContent-Length: {}\r\n",
            body.len()
        ));
    }
    request.push_str("\r\n");
    let mut bytes = request.into_bytes();
    bytes.extend_from_slice(body.unwrap_or_default());
    stream.get_mut().write_all(&bytes)?;

    let status_line = read_line(stream)?;
    let mut parts = status_line.splitn(3, ' ');
    let version = parts.next().unwrap_or_default();
    let status: u16 = parts
        .next()
        .and_then(|code| code.parse().ok())
        .ok_or_else(|| malformed("status line"))?;
    let mut length = None;
    let mut chunked = false;
    let mut keep_alive = version == "HTTP/1.1";
    loop {
        let line = read_line(stream)?;
        if line.is_empty() {
            break;
        }
        let (name, value) = line.split_once(':').ok_or_else(|| malformed("header"))?;
        let value = value.trim();
        match name.trim().to_ascii_lowercase().as_str() {
            "content-length" => {
                length = Some(value.parse::<usize>().map_err(|_| malformed("length"))?)
            }
            "transfer-encoding" => chunked = value.eq_ignore_ascii_case("chunked"),
            "connection" => keep_alive = !value.eq_ignore_ascii_case("close"),
            _ => {}
        }
    }
    let mut content = Vec::new();
    if chunked {
        loop {
            let size_line = read_line(stream)?;
            let size = usize::from_str_radix(size_line.split(';').next().unwrap_or("").trim(), 16)
                .map_err(|_| malformed("chunk size"))?;
            if size == 0 {
                while !read_line(stream)?.is_empty() {}
                break;
            }
            let start = content.len();
            content.resize(start + size, 0);
            stream.read_exact(&mut content[start..])?;
            read_line(stream)?;
        }
    } else if let Some(length) = length {
        content.resize(length, 0);
        stream.read_exact(&mut content)?;
    } else {
        stream.read_to_end(&mut content)?;
        keep_alive = false;
    }
    Ok((status, content, keep_alive))
}

fn read_line(stream: &mut BufReader<Socket>) -> io::Result<String> {
    let mut line = Vec::new();
    if stream.read_until(b'\n', &mut line)? == 0 {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "the Daemon closed the connection",
        ));
    }
    while matches!(line.last(), Some(b'\n' | b'\r')) {
        line.pop();
    }
    String::from_utf8(line).map_err(|_| malformed("non-UTF-8 header"))
}

fn malformed(what: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, format!("malformed HTTP {what}"))
}
