mod application;

use clapham_mcp_server::{Authenticator, Credential, OAuthIntrospector, Server};
use std::path::Path;
use std::process::ExitCode;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

const HELP: &str = "A London Project example: an item list served over MCP stdio.

Usage: items-mcp --configuration FILE

  --configuration FILE  Read the OAuth resource binding and the exposed operations from FILE.
  -h, --help            Show this help.

The launcher supplies the connection's token and the introspection credentials:

  ITEMS_ACCESS_TOKEN       The bearer token whose grant every call on this connection carries.
  ITEMS_INTROSPECTION_URL  The authorisation server's RFC 7662 introspection endpoint.
  ITEMS_CLIENT_ID          This resource server's client ID at that endpoint.
  ITEMS_CLIENT_SECRET      This resource server's client secret at that endpoint.

The token is verified once, before the server answers. Each launch starts a fresh list.
Stdout carries MCP; diagnostics go to stderr.";

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let file = match args
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["--help" | "-h"] => {
            println!("{HELP}");
            return ExitCode::SUCCESS;
        }
        ["--configuration", file] => Path::new(file).to_owned(),
        _ => {
            eprintln!("{HELP}");
            return ExitCode::from(2);
        }
    };
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("a current-thread runtime");
    let result = runtime.block_on(run(&file));
    // Console input may retain a blocking read after protocol shutdown.
    runtime.shutdown_background();
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("items-mcp: {error}");
            ExitCode::FAILURE
        }
    }
}

fn variable(name: &str) -> Result<String> {
    std::env::var(name).map_err(|_| format!("{name} is not set").into())
}

async fn run(file: &Path) -> Result<()> {
    let json = std::fs::read_to_string(file)
        .map_err(|error| format!("cannot read {}: {error}", file.display()))?;
    let set =
        application::compose(&json).map_err(|error| format!("{}: {error}", file.display()))?;
    let resource = set
        .oauth_resource()
        .ok_or_else(|| format!("{} binds no OAuth resource server", file.display()))?
        .clone();
    let verifier = OAuthIntrospector::new(
        resource,
        variable("ITEMS_INTROSPECTION_URL")?,
        variable("ITEMS_CLIENT_ID")?,
        variable("ITEMS_CLIENT_SECRET")?,
    )?;
    let token = variable("ITEMS_ACCESS_TOKEN")?;
    let authority = verifier
        .authenticate(&Credential::new(format!("Bearer {token}")))
        .await
        .ok_or("the authorisation server did not verify ITEMS_ACCESS_TOKEN")?;
    // Selecting the item operations leaves Clapham's built-ins available to discovery only.
    let items: Vec<_> = set
        .operations()
        .map(|operation| operation.name.to_string())
        .filter(|name| name.starts_with("items."))
        .collect();
    Server::new(set, authority)?
        .select(items)?
        .serve_stdio()
        .await?
        .waiting()
        .await?;
    Ok(())
}

#[cfg(test)]
mod tests;
