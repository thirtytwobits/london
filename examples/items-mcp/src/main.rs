mod application;

use clapham::{Principal, PrincipalKind};
use std::process::ExitCode;

const HELP: &str = "A London Project example: an in-memory list served over MCP stdio.

Usage: items-mcp --principal <agent|human>

  --principal KIND  Bind this connection to the launcher's chosen principal kind.
  -h, --help        Show this help.

Each launch starts a fresh list. Stdout carries MCP; diagnostics go to stderr.";

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let kind = match args
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["--help" | "-h"] => {
            println!("{HELP}");
            return ExitCode::SUCCESS;
        }
        ["--principal", "agent"] => PrincipalKind::Agent,
        ["--principal", "human"] => PrincipalKind::Human,
        _ => {
            eprintln!("{HELP}");
            return ExitCode::from(2);
        }
    };
    let result = run(Principal::new("operator", kind));
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("items-mcp: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run(principal: Principal) -> Result<(), Box<dyn std::error::Error>> {
    let server = application::server(principal)?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    let result = runtime.block_on(async {
        server.serve_stdio().await?.waiting().await?;
        Ok(())
    });
    // Console input may retain a blocking read after protocol shutdown.
    runtime.shutdown_background();
    result
}

#[cfg(test)]
mod tests;
