// Offline shader census: build a native probe for the host and run it on a game pack.
// The Cargo.toml written per tree by run/census.sh path-depends on the worktree
// under test.
//   census-probe <pack>
// Walks the pack's SGD5 records and raw DXBC containers (dedup by blake3 of the
// DXIL part), and for each native program runs the same stages as
// emit::translate, on a host of VICTORY_HOST_WAVE lanes (32 by default). One line
// per program on stdout:
//   <class> <hash16> <sgd5 name|@offset> <reason>
//   class = translated | untranslated | refused-wave64
//   hash16 = blake3(container)[..16], the `hash=` of the run logs
//   refused-wave64 = the emitted PSV0 requires a wave wider than the host's
//     (vkd3d: "Required WaveSize range [64, 64], but supported range is [32, 32]")
// Exits 0 even when programs fail; non-zero only if the tool itself fails.
//
// `--cache <dir>` is the warm-up: it walks the same containers, translates each
// distinct one through emit::translate (what the game's shader_cache::host_shader
// calls), encodes it as shader_cache.rs does and writes it under
// <dir>/<2 hex>/<62 hex> -- the cache a run of a tree keyed by translator sources
// reads. It never overwrites an entry; an entry already there is compared with what
// it would write. Needs a tree whose victory-shader has SOURCE_HASH/SWITCHES (the
// source-key line); the census build enables the `source-key` feature for those.
// Other options: --encoding <shader_cache.rs>, --view-limit <null slot>,
// --repeat-switch <NAME>, --dry-run, --limit <n>.
use std::collections::HashSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use victory_game::sgd5;
use victory_shader::{
    Container, FourCC, NativeProgram, emit, io, native_programs, resource, spill, structure,
};

/// (min, max) of the PSV0 wave-size range of an emitted DXIL container.
fn psv_wave_range(dxil: &[u8]) -> Option<(u32, u32)> {
    let c = Container::parse(dxil).ok()?;
    let p = c.part(FourCC(*b"PSV0"))?.data;
    let info = &p[4..];
    let u = |o: usize| u32::from_le_bytes(info[o..o + 4].try_into().unwrap());
    Some((u(16), u(20)))
}

/// The stages emit::translate runs, for one native program of a container.
fn translate_program(c: &Container, p: &NativeProgram) -> Result<emit::HostShader, String> {
    let prog = spill::build(p).map_err(|e| e.to_string())?;
    let st = structure::structure(&prog);
    let res = resource::track(&prog, &p.layout, p.stage, st.as_ref().ok())
        .map_err(|e| format!("resources: {e}"))?;
    let io = io::raise(&prog, p).map_err(|e| format!("io: {e}"))?;
    let st = st.map_err(|e| format!("structure: {e}"))?;
    emit::emit(c, p, &prog, &st, &io, &res).map_err(|e| format!("emit: {e}"))
}

fn census_one(name: &str, bytes: &[u8], seen: &mut HashSet<blake3::Hash>, host_wave: u32) {
    let Ok(c) = Container::parse(bytes) else { return }; // not a shader container
    let Some(dxil) = c.part(FourCC::DXIL) else { return }; // SM5 or older: no bitcode
    // Containers that differ only around the code are translated once.
    if !seen.insert(blake3::hash(dxil.data)) {
        return;
    }
    let hash = blake3::hash(bytes).to_hex()[..16].to_owned();
    let name = name.replace(|c: char| c.is_whitespace(), "_");
    let progs = match native_programs(dxil.data) {
        Ok(p) => p,
        Err(e) => {
            println!("untranslated {hash} {name} native: {e}");
            return;
        }
    };
    if progs.is_empty() {
        println!("untranslated {hash} {name} native: no programs");
        return;
    }
    for p in &progs {
        let stage = format!("{:?}", p.stage).to_lowercase();
        match translate_program(&c, p) {
            Err(e) => println!("untranslated {hash} {name} {stage}: {e}"),
            Ok(s) => match psv_wave_range(&s.dxil) {
                Some((lo, hi)) if lo > host_wave => println!(
                    "refused-wave64 {hash} {name} {stage}: WaveSize [{lo}, {hi}] on {host_wave}-lane host"
                ),
                _ => println!("translated {hash} {name} {stage}"),
            },
        }
    }
}

/// The cache warm-up's arguments; parsed even on trees without a source key so
/// the option can say why it cannot work there.
struct CacheArgs {
    dir: PathBuf,
    encoding: PathBuf,
    view_limit: u32,
    repeats: Vec<String>,
    dry_run: bool,
    limit: Option<usize>,
}

/// What happened to one computed entry.
#[derive(Default)]
struct CacheStats {
    containers: u64,
    translated: u64,
    failed: u64,
    written: u64,
    existing_identical: u64,
    existing_different: u64,
    missing: u64,
}

#[cfg(feature = "source-key")]
struct Warm<'a> {
    dir: &'a Path,
    build: &'a [u8; 32],
    dry_run: bool,
    stats: CacheStats,
}

#[cfg(feature = "source-key")]
impl Warm<'_> {
    fn one(&mut self, container: &[u8]) -> Result<(), String> {
        self.stats.containers += 1;
        match emit::translate(container) {
            Err(e) => {
                self.stats.failed += 1;
                eprintln!(
                    "cache-skip {} {e}",
                    &blake3::hash(container).to_hex()[..16]
                );
                Ok(())
            }
            Ok(shader) => {
                self.stats.translated += 1;
                let entry = encode(&shader);
                let path = cache_path(self.dir, self.build, container);
                match store(&path, &entry, self.dry_run)? {
                    Stored::Written => self.stats.written += 1,
                    Stored::Same => self.stats.existing_identical += 1,
                    Stored::Different => {
                        self.stats.existing_different += 1;
                        eprintln!("cache-different {}", path.display());
                    }
                    Stored::Missing => self.stats.missing += 1,
                }
                Ok(())
            }
        }
    }
}

#[cfg(feature = "source-key")]
fn build_fingerprint(encoding: &[u8], view_limit: u32, repeats: &[String]) -> [u8; 32] {
    // Only the translator-source line has both sources and switches. set_view_heap_limit has
    // already run: the getter is hashed, as the game hashes it after probe_view_limit.
    let mut bytes = victory_shader::SOURCE_HASH.as_bytes().to_vec();
    bytes.extend_from_slice(blake3::hash(encoding).as_bytes());
    bytes.extend_from_slice(&emit::loop_limit().to_le_bytes());
    debug_assert_eq!(emit::view_heap_limit(), view_limit);
    bytes.extend_from_slice(&emit::view_heap_limit().to_le_bytes());
    bytes.push(emit::group_sync());
    // Every switch the translator's sources name, in SWITCHES' order, then the extra one some
    // trees list again after them (a duplicate in their key).
    for name in victory_shader::SWITCHES
        .split(',')
        .chain(repeats.iter().map(String::as_str))
    {
        bytes.extend_from_slice(name.as_bytes());
        bytes.push(b'=');
        if let Some(value) = std::env::var_os(name) {
            bytes.extend_from_slice(value.as_encoded_bytes());
        }
        bytes.push(0);
    }
    bytes.extend_from_slice(&emit::shader_opt().to_le_bytes());
    *blake3::hash(&bytes).as_bytes()
}

/// The key of victory-pipeline's CacheKey::new(Translate{id:"shader.dxil",
/// version:1}, build ++ container), laid out as Cache::path_for does.
fn cache_path(dir: &Path, build: &[u8; 32], container: &[u8]) -> PathBuf {
    let mut h = blake3::Hasher::new();
    h.update(b"shader.dxil");
    h.update(&[0]);
    h.update(&1u32.to_le_bytes());
    h.update(build);
    h.update(container);
    let hex = h.finalize().to_hex();
    dir.join(&hex[..2]).join(&hex[2..])
}

enum Stored {
    Written,
    Same,
    Different,
    Missing,
}

/// Writes `bytes` to `path` unless it is already there (a no-clobber hard link
/// of a staging file: a reader never sees a partial entry). In a dry run only
/// compares. Returns how `path` relates to the computed entry.
fn store(path: &Path, bytes: &[u8], dry_run: bool) -> Result<Stored, String> {
    match std::fs::read(path) {
        Ok(old) => {
            return Ok(if old == bytes {
                Stored::Same
            } else {
                Stored::Different
            });
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(format!("reading {}: {e}", path.display())),
    }
    if dry_run {
        return Ok(Stored::Missing);
    }
    let parent = path
        .parent()
        .ok_or_else(|| format!("{} has no parent", path.display()))?;
    std::fs::create_dir_all(parent).map_err(|e| format!("creating {}: {e}", parent.display()))?;
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let tmp = parent.join(format!(
        ".tmp-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    let result = (|| -> std::io::Result<Stored> {
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp)?;
        file.write_all(bytes)?;
        drop(file);
        match std::fs::hard_link(&tmp, path) {
            Ok(()) => Ok(Stored::Written),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                let old = std::fs::read(path)?;
                Ok(if old == bytes {
                    Stored::Same
                } else {
                    Stored::Different
                })
            }
            Err(e) => Err(e),
        }
    })();
    let _ = std::fs::remove_file(&tmp);
    result.map_err(|e| format!("writing {}: {e}", path.display()))
}

/// Mirrors victory-gpu/src/shader_cache.rs::encode (VSH1); keep it in step with
/// the tree. The key hashes that file's text, so a changed encoder invalidates
/// older entries; the byte comparison here catches a probe that fell behind.
#[cfg(feature = "source-key")]
fn encode(s: &emit::HostShader) -> Vec<u8> {
    use victory_shader::{HwStage, RootType};

    const MAGIC: &[u8; 4] = b"VSH1";
    const STAGES: [HwStage; 8] = [
        HwStage::Ls,
        HwStage::Hs,
        HwStage::Es,
        HwStage::Gs,
        HwStage::Vs,
        HwStage::Ps,
        HwStage::Ms,
        HwStage::Cs,
    ];
    fn root_code(r: RootType) -> u32 {
        match r {
            RootType::Constants => 0,
            RootType::Descriptor => 1,
            RootType::Table => 2,
            RootType::ImmediateSampler => 3,
            RootType::ResourceHeap => 4,
            RootType::SamplerHeap => 5,
            RootType::Other(x) => 6 + x,
        }
    }

    let mut out = MAGIC.to_vec();
    let word = |out: &mut Vec<u8>, v: u32| out.extend(v.to_le_bytes());
    let stage = STAGES.iter().position(|&x| x == s.stage).unwrap_or(0) as u32;
    word(&mut out, stage);
    word(&mut out, s.dxil.len() as u32);
    out.extend(&s.dxil);
    word(&mut out, s.user_data.len() as u32);
    for u in &s.user_data {
        for v in [u.index, u32::from(u.slot), root_code(u.root), u.dword] {
            word(&mut out, v);
        }
    }
    word(&mut out, s.samplers.len() as u32);
    for sampler in &s.samplers {
        for &v in sampler {
            word(&mut out, v);
        }
    }
    word(&mut out, s.static_samplers.len() as u32);
    for t in &s.static_samplers {
        let [u, v, w] = t.address;
        let words = [
            t.filter,
            u,
            v,
            w,
            t.mip_lod_bias.to_bits(),
            t.max_anisotropy,
            t.comparison,
            t.border,
            t.min_lod.to_bits(),
            t.max_lod.to_bits(),
        ];
        for v in words {
            word(&mut out, v);
        }
    }
    for slots in [&s.root_cbvs, &s.root_srvs, &s.root_uavs] {
        word(&mut out, slots.len() as u32);
        out.extend(slots);
    }
    word(&mut out, s.root_indexed_views.len() as u32);
    for view in &s.root_indexed_views {
        for value in [
            u32::from(view.table_slot),
            u32::from(view.constant_slot),
            view.constant_dword,
            view.byte_offset,
            u32::from(view.shift),
        ] {
            word(&mut out, value);
        }
    }
    out
}

#[cfg(feature = "source-key")]
fn offer(
    bytes: &[u8],
    seen: &mut HashSet<blake3::Hash>,
    warm: &mut Warm,
    containers: &mut usize,
    limit: Option<usize>,
) -> Result<bool, String> {
    if !seen.insert(blake3::hash(bytes)) {
        return Ok(false);
    }
    warm.one(bytes)?;
    *containers += 1;
    Ok(limit.is_some_and(|n| *containers >= n))
}

#[cfg(feature = "source-key")]
fn fill_cache(data: &[u8], args: &CacheArgs) -> Result<(), String> {
    let encoding = std::fs::read(&args.encoding)
        .map_err(|e| format!("cannot read {}: {e}", args.encoding.display()))?;
    emit::set_view_heap_limit(args.view_limit);
    let build = build_fingerprint(&encoding, args.view_limit, &args.repeats);
    std::fs::create_dir_all(&args.dir)
        .map_err(|e| format!("creating {}: {e}", args.dir.display()))?;
    let started = std::time::Instant::now();
    let mut warm = Warm {
        dir: &args.dir,
        build: &build,
        dry_run: args.dry_run,
        stats: CacheStats::default(),
    };
    let mut seen: HashSet<blake3::Hash> = HashSet::new();
    let mut containers = 0usize;
    let mut limited = false;

    if sgd5::is_sgd5(data) {
        for r in sgd5::records(data) {
            let Some(b) = r.unpack() else { continue };
            limited = offer(&b, &mut seen, &mut warm, &mut containers, args.limit)?;
            if limited {
                break;
            }
        }
    }
    if !limited {
        for off in memchr::memmem::find_iter(data, b"DXBC") {
            if Container::parse(&data[off..]).is_ok() && off + 28 <= data.len() {
                let n = u32::from_le_bytes(data[off + 24..off + 28].try_into().unwrap()) as usize;
                if n > 0 && off + n <= data.len() {
                    limited = offer(
                        &data[off..off + n],
                        &mut seen,
                        &mut warm,
                        &mut containers,
                        args.limit,
                    )?;
                    if limited {
                        break;
                    }
                }
            }
        }
    }

    let stats = &warm.stats;
    println!(
        "cache {}: key={} containers={} translated={} failed={} written={} existing_identical={} existing_different={} missing={} dry_run={} loop_limit={} view_limit={} encoding={} elapsed={:.1}s",
        args.dir.display(),
        blake3::Hash::from(build).to_hex(),
        stats.containers,
        stats.translated,
        stats.failed,
        stats.written,
        stats.existing_identical,
        stats.existing_different,
        stats.missing,
        args.dry_run,
        emit::loop_limit(),
        args.view_limit,
        args.encoding.display(),
        started.elapsed().as_secs_f64(),
    );
    if stats.existing_different > 0 {
        return Err(format!(
            "{} entries differ from what this probe computes",
            stats.existing_different
        ));
    }
    Ok(())
}

#[cfg(not(feature = "source-key"))]
fn fill_cache(_data: &[u8], _args: &CacheArgs) -> Result<(), String> {
    Err(
        "this tree has no victory_shader::SOURCE_HASH: its cache is keyed by the whole \
         victory.exe (or not by translator sources), so an offline probe cannot fill it"
            .to_owned(),
    )
}

fn usage() -> ! {
    eprintln!(
        "usage: census-probe <pack> [--cache <shaders dir> --encoding <shader_cache.rs> \
         --view-limit <null slot> [--repeat-switch <name>] [--dry-run] [--limit <n>]]"
    );
    std::process::exit(2);
}

fn value(it: &mut impl Iterator<Item = String>, option: &str) -> String {
    it.next()
        .unwrap_or_else(|| {
            eprintln!("census-probe: {option} needs a value");
            std::process::exit(2);
        })
}

fn main() {
    let mut pack: Option<String> = None;
    let mut cache_dir: Option<PathBuf> = None;
    let mut encoding: Option<PathBuf> = None;
    let mut view_limit: Option<u32> = None;
    let mut repeats = Vec::new();
    let mut dry_run = false;
    let mut limit = None;
    let mut it = std::env::args().skip(1);
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--cache" => cache_dir = Some(PathBuf::from(value(&mut it, "--cache"))),
            "--encoding" => encoding = Some(PathBuf::from(value(&mut it, "--encoding"))),
            "--view-limit" => {
                view_limit = Some(
                    value(&mut it, "--view-limit")
                        .parse()
                        .unwrap_or_else(|_| usage()),
                )
            }
            "--repeat-switch" => repeats.push(value(&mut it, "--repeat-switch")),
            "--dry-run" => dry_run = true,
            "--limit" => {
                limit = Some(
                    value(&mut it, "--limit")
                        .parse()
                        .unwrap_or_else(|_| usage()),
                )
            }
            "-h" | "--help" => usage(),
            s if s.starts_with('-') => {
                eprintln!("census-probe: unknown option {s}");
                usage();
            }
            s => {
                if pack.replace(s.to_owned()).is_some() {
                    eprintln!("census-probe: more than one pack");
                    usage();
                }
            }
        }
    }
    let Some(pack) = pack else { usage() };
    if cache_dir.is_some() {
        let (Some(encoding), Some(view_limit)) = (encoding, view_limit) else {
            eprintln!("census-probe: --cache needs --encoding and --view-limit");
            usage();
        };
        if view_limit == 0 {
            eprintln!("census-probe: --view-limit must be the game's null slot (not 0)");
            usage();
        }
        let args = CacheArgs {
            dir: cache_dir.expect("checked"),
            encoding,
            view_limit,
            repeats,
            dry_run,
            limit,
        };
        let host_wave: u32 = std::env::var("VICTORY_HOST_WAVE")
            .ok()
            .and_then(|v| v.parse().ok())
            .filter(|&n| n > 0)
            .unwrap_or(32);
        emit::set_host_max_wave(host_wave);
        let data = std::fs::read(&pack).unwrap_or_else(|e| {
            eprintln!("census-probe: cannot read {pack}: {e}");
            std::process::exit(2);
        });
        if let Err(e) = fill_cache(&data, &args) {
            eprintln!("census-probe: {e}");
            std::process::exit(1);
        }
        return;
    }

    let host_wave: u32 = std::env::var("VICTORY_HOST_WAVE")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|&n| n > 0)
        .unwrap_or(32);
    emit::set_host_max_wave(host_wave);
    let data = std::fs::read(&pack).unwrap_or_else(|e| {
        eprintln!("census-probe: cannot read {pack}: {e}");
        std::process::exit(2);
    });
    let mut seen: HashSet<blake3::Hash> = HashSet::new();
    // The packed records of SGD5 data, and every container stored as is, found
    // by its magic (the same sources as `victory translate`).
    if sgd5::is_sgd5(&data) {
        for r in sgd5::records(&data) {
            if let Some(b) = r.unpack() {
                census_one(r.name.unwrap_or("?"), &b, &mut seen, host_wave);
            }
        }
    }
    for off in memchr::memmem::find_iter(&data, b"DXBC") {
        if Container::parse(&data[off..]).is_ok() && off + 28 <= data.len() {
            let n = u32::from_le_bytes(data[off + 24..off + 28].try_into().unwrap()) as usize;
            if n > 0 && off + n <= data.len() {
                census_one(&format!("@{off:#x}"), &data[off..off + n], &mut seen, host_wave);
            }
        }
    }
}
