fn main() {
    println!("cargo:rerun-if-changed=../Cargo.lock");

    // Keep the dependency identity in the binary for benchmark provenance.
    let lockfile = include_str!("../Cargo.lock");
    let revision = lockfile
        .split("\n[[package]]\n")
        .find(|package| package.starts_with("name = \"threadance\"\n"))
        .and_then(|package| {
            package
                .lines()
                .find_map(|line| line.strip_prefix("source = \"git+"))
        })
        .and_then(|source| source.strip_suffix('"'))
        .and_then(|source| source.rsplit_once('#'))
        .map(|(_, revision)| revision)
        .filter(|revision| revision.len() == 40 && revision.bytes().all(|b| b.is_ascii_hexdigit()))
        .expect("Cargo.lock must contain the resolved Threadance Git commit");

    println!("cargo:rustc-env=THREADANCE_GIT_COMMIT={revision}");
}
