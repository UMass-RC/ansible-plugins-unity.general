## unity general ansible plugins

NOTE: the Github repo is only a mirror of [the Gitlab repo](https://gitlab.rc.umass.edu/unity/ansible-collections/general)

### bitwarden

* add caching/locking to make bitwarden lookups fast and safe in parallel
    * for security, by default this is stored in `/dev/shm/` on linux machines and `tmpdisk` is recommended on macos
* set default bitwarden collection ID to enforce that your bitwarden records are actually in the collection they're supposed to be in
* allow downloading attachments to remote host, with permissions/owner/group required and `no_log: true` as the default
* better error messages

### stdout callback plugins

* don't display duplicate results and diffs
* condense host lists into "folded node sets" using Clustershell python API
* don't display every result on one line, instead keep a single line of "status totals" that is updated in real time using carriage return
* print the hostnames of any running runners when KeyboardInterrupt is received, so you can exclude nodes that block your playbook
* pipe diffs through formatter (`delta`, `diffr`, ...)

### slack

* let other plugins add lines to a slack message sent using the python API at the end of a playbook

### html upload

* use `aha` to make an HTML document very similar to command line output, complete with all colors, upload that document to a webserver
* read bitwarden cache to ensure that no secrets are uploaded
* when combined with diff formatting and slack, this makes it very easy to share the results of your playbook with your team
