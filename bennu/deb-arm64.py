"""Python check for the deb-arm64 item."""


def judge(b: dict) -> tuple[bool, str]:
    """Pass only if cpack itself built an arm64 deb: no relabel, file name ends _arm64.deb."""
    if "relabel_needed" not in b:
        return False, "the build manifest has no relabel_needed: not an arm64 deb build"
    name = b.get("deb_file", "")
    seen = f"{name}: Architecture {b.get('deb_arch')}"
    problems = []
    if b["relabel_needed"]:
        problems.append("burger had to relabel the Architecture (cpack did not write arm64)")
    if not name.endswith("_arm64.deb"):
        problems.append("file name does not end _arm64.deb")
    if problems:
        return False, f"{'; '.join(problems)} ({seen})"
    return True, f"native arm64 deb, no relabel ({seen})"


def judge_deb_arm64(ctx):
    return judge(ctx.manifest.get("bennu") or {})
