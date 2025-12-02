# Shibboleth

Configures shibboleth service provider (SP). Also see `update-incommon-metadata.yml`.

warning: if this host is not added as a `Location` to the shib metadata, the UMass active directory shib identity provider (IDP) will redirect to another site. I speculate that it just picks the first `Location` in the metadata for the given `entityID`.

```
$ grep unity.rc.umass.edu InCommon-metadata.xml | pcregrep -o1 'Location="https://(.*?unity.rc.umass.edu.*?)/.*"' | sort -u
coldfront-dev.unity.rc.umass.edu
ood-dev.unity.rc.umass.edu
ood.unity.rc.umass.edu
unity.rc.umass.edu
web-dev.unity.rc.umass.edu
xdmod.unity.rc.umass.edu
```

This role should not interfere with web server configuration, leave that to the web server role.

`apt` package manager, systemd are required.

### Parameters

```yml
shib_enable_supervisor:
  type: bool
  notes:
    - "if enabled, use `supervisor` to make sure that `shibauthorizer` and `shibresponder`"
    - "are always running"
```
