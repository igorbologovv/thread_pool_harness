use std::{
    fs::{File, OpenOptions},
    io::{self, BufRead, BufReader, Write},
    path::Path,
};

/// Controls an external `perf stat` process through its control and
/// acknowledgement FIFOs.
pub struct PerfControl {
    control: File,
    ack: BufReader<File>,
}

impl PerfControl {
    pub fn connect(control_path: impl AsRef<Path>, ack_path: impl AsRef<Path>) -> io::Result<Self> {
        let control = OpenOptions::new().write(true).open(control_path)?;

        let ack = OpenOptions::new().read(true).open(ack_path)?;

        Ok(Self {
            control,
            ack: BufReader::new(ack),
        })
    }

    pub fn enable(&mut self) -> io::Result<()> {
        self.send_command("enable")
    }

    pub fn disable(&mut self) -> io::Result<()> {
        self.send_command("disable")
    }

    fn send_command(&mut self, command: &str) -> io::Result<()> {
        writeln!(self.control, "{command}")?;
        self.control.flush()?;

        let mut response = String::new();

        if self.ack.read_line(&mut response)? == 0 {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "perf acknowledgement FIFO closed",
            ));
        }

        let response = response.trim_matches(|c: char| c == '\0' || c.is_whitespace());

        if response != "ack" {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("unexpected perf response: {response:?}"),
            ));
        }

        Ok(())
    }
}
