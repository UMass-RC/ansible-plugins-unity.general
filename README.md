## unity general ansible plugins

NOTE: the Github repo is only a mirror of [the Gitlab repo](https://gitlab.rc.umass.edu/unity/ansible-collections/general)

#### bitwarden

* add caching/locking to make bitwarden lookups fast and safe in parallel
    * for security, by default this is stored in `/dev/shm/` on linux machines and `tmpdisk` is recommended on macos
* set default bitwarden collection ID to enforce that your bitwarden records are actually in the collection they're supposed to be in
* allow downloading attachments to remote host, with permissions/owner/group required and `no_log: true` as the default
* better error messages

#### stdout callback plugins

* don't display duplicate results and diffs
* condense host lists into "folded node sets" using Clustershell python API
* don't display every result on one line, instead keep a single line of "status totals" that is updated in real time using carriage return
* print the hostnames of any running runners when KeyboardInterrupt is received, so you can exclude nodes that block your playbook
* pipe diffs through formatter (`delta`, `diffr`, ...)

![dedupe and diff formatting example](https://gitlab.rc.umass.edu/-/project/81/uploads/e08ed56f1911a1d306aba4ef26a20c25/image.png)

in the above example, the `apt` diff has been deduplicated for a whole list of hosts, and diffs are piped through `delta`. This is a screenshot taken from a web browser, since the log was converted to HTML using `aha` and uploaded to a web server using `http_post`.

#### slack

* let other plugins add lines to a slack message sent using the python API at the end of a playbook

#### html upload

* use `aha` to make an HTML document very similar to command line output, complete with all colors, upload that document to a webserver
* read bitwarden cache to ensure that no secrets are uploaded
* when combined with diff formatting and slack, this makes it very easy to share the results of your playbook with your team

### install

* `mkdir -p /path/to/ansible_collections/unity/general`
* `git clone <this-repo> /path/to/ansible_collections/unity/general`
* `export ANSIBLE_COLLECTIONS_PATH=/path/to/ansible_collections:$ANSIBLE_COLLECTIONS_PATH`

### list plugins
```sh
$ ansible-doc --metadata-dump unity.general 2>/dev/null | jq -C '.all | with_entries(select(.key != "keyword" and (.value | keys | length) > 0) | .value |= keys)'
```
```json
{
  "callback": [
    "unity.general.cron",
    "unity.general.deduped_default",
    "unity.general.http_post",
    "unity.general.slack",
    "unity.general.status_oneline"
  ],
  "lookup": [
    "unity.general.bitwarden",
    "unity.general.bitwarden_attachment_download"
  ],
  "module": [
    "unity.general.bitwarden_copy_attachment"
  ]
}
```

### view documentation for plugin
```sh
ansible-doc -t callback unity.general.cron
```
