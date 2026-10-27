mod application;

use clapham::ComposedSet;
use clapham_mcp_server::{Authenticator, Credential, HttpServer, OAuthIntrospector, Server};
use std::net::SocketAddr;
use std::path::Path;
use std::process::ExitCode;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

const HELP: &str = "A London Project example: an item list served over MCP.

Usage: items-mcp --configuration FILE [--http ADDRESS]

  --configuration FILE  Read the OAuth resource binding and the exposed operations from FILE.
  --http ADDRESS        Serve MCP over Streamable HTTP on ADDRESS, a loopback address and port,
                        instead of over stdio.
  -h, --help            Show this help.

The launcher supplies the introspection credentials:

  ITEMS_INTROSPECTION_URL  The authorisation server's RFC 7662 introspection endpoint.
  ITEMS_CLIENT_ID          This resource server's client ID at that endpoint.
  ITEMS_CLIENT_SECRET      This resource server's client secret at that endpoint.

Over stdio, the launcher also supplies the connection's token:

  ITEMS_ACCESS_TOKEN       The bearer token whose grant every call on this connection carries.

It is verified once, before the server answers; stdout carries MCP and the list lasts for the
connection. Over Streamable HTTP, each request's bearer token is verified before the request is
read; stdout carries the MCP endpoint's URL and the list lasts until the server is interrupted.
Each launch starts a fresh list. Diagnostics go to stderr.";

enum Transport {
    Stdio,
    Http(String),
}

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let (file, transport) = match args
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["--help" | "-h"] => {
            println!("{HELP}");
            return ExitCode::SUCCESS;
        }
        ["--configuration", file] => (Path::new(file).to_owned(), Transport::Stdio),
        ["--configuration", file, "--http", address] => (
            Path::new(file).to_owned(),
            Transport::Http((*address).to_owned()),
        ),
        _ => {
            eprintln!("{HELP}");
            return ExitCode::from(2);
        }
    };
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("a current-thread runtime");
    let result = runtime.block_on(run(&file, transport));
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

async fn run(file: &Path, transport: Transport) -> Result<()> {
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
    // Selecting the item operations leaves Clapham's built-ins available to discovery only.
    let items: Vec<_> = set
        .operations()
        .map(|operation| operation.name.to_string())
        .filter(|name| name.starts_with("items."))
        .collect();
    match transport {
        Transport::Stdio => serve_stdio(set, items, verifier).await,
        Transport::Http(address) => serve_http(set, items, verifier, &address).await,
    }
}

async fn serve_stdio(
    set: ComposedSet,
    items: Vec<String>,
    verifier: OAuthIntrospector,
) -> Result<()> {
    let token = variable("ITEMS_ACCESS_TOKEN")?;
    let authority = verifier
        .authenticate(&Credential::new(format!("Bearer {token}")))
        .await
        .ok_or("the authorisation server did not verify ITEMS_ACCESS_TOKEN")?;
    Server::new(set, authority)?
        .select(items)?
        .serve_stdio()
        .await?
        .waiting()
        .await?;
    Ok(())
}

async fn serve_http(
    set: ComposedSet,
    items: Vec<String>,
    verifier: OAuthIntrospector,
    address: &str,
) -> Result<()> {
    let address: SocketAddr = address
        .parse()
        .map_err(|_| format!("--http {address} is not an address and port"))?;
    // The server speaks plain HTTP, and bearer tokens must not cross a network unencrypted.
    if !address.ip().is_loopback() {
        return Err(format!("--http {address} is not a loopback address").into());
    }
    let router = HttpServer::new(set, verifier)?.select(items)?.into_router();
    let listener = tokio::net::TcpListener::bind(address).await?;
    println!("http://{}/mcp", listener.local_addr()?);
    axum::serve(listener, router)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}

#[cfg(test)]
mod tests;
