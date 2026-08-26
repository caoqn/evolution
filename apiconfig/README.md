# Private API Configuration

This backup intentionally contains no provider credentials.

Create one private configuration file for each benchmark that will run:

```bash
cp apiconfig/example.env apiconfig/gaia.env
cp apiconfig/example.env apiconfig/locobench.env
cp apiconfig/example.env apiconfig/beyond.env
cp apiconfig/example.env apiconfig/loca.env
```

Set `DEV_API_BASE`, `DEV_API_KEY`, `DEV_MODEL`, and `META_TEAM_MODEL` in each
file. Keep assignments in shell-compatible form with no spaces around `=`.
These files are ignored by Git and must not be copied into public archives.
