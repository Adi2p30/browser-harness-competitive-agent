---
title: Pipeline Stages
---

# Pipeline Stages

What `field_pipeline` does per row, in order (`agent-workspace/field_pipeline/__main__.py`'s
own module docstring):

1. [discover](/okf/project/stages/discover.md)
2. [fetch](/okf/project/stages/fetch.md)
3. [chunking](/okf/project/stages/chunking.md)
4. [retrieve](/okf/project/stages/retrieve.md)
5. [extract](/okf/project/stages/extract.md)
6. [validate](/okf/project/stages/validate.md)
7. [ipeds](/okf/project/stages/ipeds.md) (federal data, no browser/LLM, fills a few fields only)
8. [write](/okf/project/stages/write.md)
