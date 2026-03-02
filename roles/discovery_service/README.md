Installs the Unity shibboleth discovery service. Note that only one discovery service is required: OOD can use the webportal as its `shibRequestSetting discoveryUrl`.

variables:
* `shib_SPs`: list of hosts that the discovery service is allowed to redirect to
    * can also have a port number: `hostname:xxxx`
    * this is enforced in clientside javascript, so doesn't provide any security
* `shib_preferred_IDPs`: IDPs which have their logos displayed on the front of the discovery page

To configure the logos on the front page, you will have to modify the tasks in `tasks/main.yml`.
