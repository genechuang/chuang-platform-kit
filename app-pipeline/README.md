# chuang-app-pipeline

An Expo app's build, check and publish pipeline, project-agnostic. A distribution of its own in `app-pipeline/` of
chuang-platform-kit's repository, with its own tags (`app-v<version>`), pinned only where an app's pipeline runs:

    chuang-app-pipeline @ git+https://github.com/genechuang/chuang-platform-kit@app-v0.1.0#subdirectory=app-pipeline

Standard library only, so a host's pipeline can load it on a bare runner from the tag's tarball with no install.

- `chuang_app_pipeline.expo_channel` (0.1.0): what an Expo update channel serves a phone, asked the way expo-updates
  asks it (the multipart manifest and its asset headers), judged against the release's own notice (the branch, the
  runtime, when it was published, the same launch bundle the device ran on the lower channel), and the bundle
  downloaded and its hash checked. The host passes its update server's address, its notice, its channels' names and
  how it reads a tree; the verdict is a dict the host stores where it keeps its own.

- `chuang_app_pipeline.artifact` (0.2.0): a build artifact's parts any app's pipeline needs. The content key a build
  is stored under (the app folder's git tree and a hash of its public config), what an export ships by kind and its
  size line, whether an export is fit to release (it names the release API and no host a release must not) and is
  this build's (its stamp), whether the installed packages are the lockfile's, the join of each platform's phone
  export into one bundle, and what a build's manifest says was tested.

Nothing here names a project, an app, a bucket or a person: those are the host's arguments.
