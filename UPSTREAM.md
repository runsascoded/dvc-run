# Upstream Strategy: Should this be in DVC?

## TL;DR

**Start as `dvc-run`, then propose to DVC once proven.**

## The Case for Merging into DVC

### Why this belongs in DVC itself:

1. **Long-standing request**: [Issue #755](https://github.com/iterative/dvc/issues/755) (2019) and [Issue #9805](https://github.com/iterative/dvc/issues/9805) (2023) show users want parallel execution

2. **Core functionality**: Parallel execution is fundamental workflow improvement, not a niche feature

3. **Better integration**: Native implementation could:
   - Share DVC's hashing/caching code
   - Avoid lock conflicts (internal coordination)
   - Be enabled with `dvc repro --parallel` or `dvc config core.parallel true`

4. **Maintenance burden**: Keeping up with DVC's evolving `dvc.yaml`/`dvc.lock` format is ongoing work

5. **User adoption**: Users trust DVC, less likely to adopt third-party tool

### Potential implementation in DVC:

```python
# Inside dvc/repo/repro.py or new dvc/repo/parallel.py

class ParallelExecutor:
    """Parallel stage executor for dvc repro."""

    def repro(self, targets, jobs=None):
        """Reproduce pipeline with parallel execution.

        Args:
            targets: Stages to reproduce
            jobs: Max parallel jobs (default: CPU count)
        """
        # Build DAG (existing code)
        graph = self.repo.index.graph

        # Group into levels (new code)
        levels = self._topological_levels(graph, targets)

        # Execute level-by-level with ThreadPoolExecutor
        for level in levels:
            with ThreadPoolExecutor(max_workers=jobs) as executor:
                futures = {
                    executor.submit(self._run_stage, stage): stage
                    for stage in level
                }
                # Handle results...
```

**CLI changes:**
```bash
# New flag to existing command
dvc repro --jobs 8
dvc repro -j 8

# Or new config option
dvc config core.parallel_jobs 8
```

## The Case for Staying Separate

### Why `dvc-run` should remain independent:

1. **Faster iteration**: No DVC approval process, can experiment freely

2. **Simpler scope**: Just execution, not data versioning, remotes, experiments, etc.

3. **Lighter dependency**: Don't need all of DVC's dependencies

4. **Alternative design philosophy**:
   - DVC: "Git for data" (versioning-first)
   - `dvc-run`: "Make for data" (execution-first)

5. **Upstream may not want it**: DVC team might have reasons for serial execution:
   - Lock file safety
   - Resource management
   - Complexity concerns

6. **Competing tools exist**: `doit`, `snakemake`, `airflow` - maybe DVC sees them as better solutions

## Hybrid Approach (Recommended)

### Phase 1: Prove Value (Now - 6 months)
- Develop `dvc-run` as standalone tool
- Test on real workloads (NJDOT pipeline)
- Get community feedback
- Ensure DVC compatibility

### Phase 2: Engage DVC Team (6-12 months)
- Open discussion issue on DVC repo
- Link to `dvc-run` as proof-of-concept
- Ask: "Would you accept a PR for parallel execution?"
- Share performance numbers and user stories

### Phase 3a: If DVC is interested
- Collaborate on design
- Port `dvc-run` logic into DVC
- Contribute as PR
- Maintain `dvc-run` as backport for older DVC versions

### Phase 3b: If DVC isn't interested
- Continue `dvc-run` as official solution
- Publish on PyPI
- Grow user base
- Become de facto standard for parallel DVC pipelines

## Research: What does DVC team think?

### Check existing discussions:

**Issue #755: "repro: add scheduler for parallelising execution jobs"**
- Opened: 2019
- Status: Open
- Comments suggest it's wanted but low priority

**Issue #9805: "Running `dvc repro` in parallel does not work"**
- Opened: 2023
- Problem: Lock conflicts when manually running parallel repro
- No official solution provided

**Documentation on parallel execution:**
- Recommends manual `dvc repro stage1 & dvc repro stage2 &`
- Acknowledges limitations
- No built-in solution

### Interpretation:

DVC team **knows about the problem** but hasn't prioritized it. Possible reasons:
- Engineering bandwidth
- Complexity concerns (lock safety)
- Preference for external orchestrators (Airflow, Prefect)
- Waiting for good design proposal

**`dvc-run` could be that design proposal!**

## Action Items

### Immediate (this PR):
- [x] Create `dvc-run` repo with clear README
- [x] Write comprehensive spec (DVC_LOCK_SUPPORT.md)
- [x] Document upstream strategy (this file)

### Short-term (next sprint):
- [x] Implement basic `dvc.lock` parsing ✅ Phase 1 complete
- [x] Implement freshness checking ✅ Phase 1 complete
- [ ] Test on NJDOT pipeline ← NEXT!
- [ ] Measure actual speedup ← NEXT!
- [ ] Document compatibility with DVC tools

### Medium-term (3-6 months):
- [x] Implement thread-safe `dvc.lock` updates ✅ Phase 2 complete
- [ ] Add validation mode (Phase 3)
- [ ] Publish to PyPI
- [ ] Write blog post with benchmarks
- [ ] Get 10+ users

### Long-term (6-12 months):
- [ ] Open DVC discussion issue
- [ ] Share `dvc-run` as proof-of-concept
- [ ] Propose design for `dvc repro --parallel`
- [ ] If accepted, create PR
- [ ] If rejected, grow `dvc-run` community

## Decision Framework

**When to propose to DVC:**

Must have:
- ✅ Working implementation in `dvc-run`
- ✅ Performance benchmarks (3x+ speedup on real pipelines)
- ✅ No compatibility issues with DVC
- ✅ 10+ real users/projects using it
- ✅ Clear design document

Nice to have:
- 🔷 Published blog post with case studies
- 🔷 PyPI package with good documentation
- 🔷 CI/CD integration examples
- 🔷 Comparison with alternatives (Airflow, Prefect)

**When to stay independent:**

If DVC team:
- ❌ Rejects the approach
- ❌ Has conflicting roadmap
- ❌ Prefers external orchestration
- ❌ Can't prioritize review/merge

Then `dvc-run` becomes the **official parallel execution engine** for DVC.

## Comparison with Historical Precedents

### Similar situations in other ecosystems:

**1. `uv` (Rust-based Python package manager)**
- Started as alternative to `pip`
- Proved value (10-100x faster)
- Now: pip team exploring integration ideas
- Lesson: Prove value first, upstream later

**2. `esbuild` (JavaScript bundler)**
- Alternative to webpack (100x faster)
- Stayed independent
- Influenced webpack v5 design
- Lesson: Good ideas get adopted even without merge

**3. `ripgrep` (Rust grep)**
- Alternative to GNU grep (100x faster)
- Stayed independent
- Now: de facto standard
- Lesson: Better tool wins, regardless of "official" status

**Our situation is most like `uv`:**
- Drop-in replacement for existing tool
- Same functionality, much faster
- Opportunity for upstream integration
- Value in both outcomes (merge or independence)

## Conclusion

**Start independent, stay open to upstream integration.**

The spec in `DVC_LOCK_SUPPORT.md` is designed to be either:
1. A complete implementation in `dvc-run`, or
2. A design document for a DVC PR

We win either way:
- **If merged**: DVC users get parallel execution natively
- **If independent**: `dvc-run` becomes the standard solution

Next step: **Build it, prove it works, measure the speedup.**
