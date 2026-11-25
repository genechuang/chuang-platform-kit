"""chuang-app-pipeline: an Expo app's build, check and publish pipeline, project-agnostic (SMADPickleBot #2161). Since
0.1.0 `expo_channel`: what an update channel serves a phone, asked as expo-updates asks it and judged against a
release's own notice. Since 0.2.0 `artifact`: a build's key, what an export ships, whether it is fit to release and
is this build's, the join of each platform's export, and what a manifest says was tested (#2237). Since 0.3.0
`runtime`: the native runtime guard's decision, its memo, the package checks and a store read tried again (#2244). Since
0.4.0 `gradle` and `android`: a local Android build's Gradle side and a device read (#2310). A
distribution of its own beside chuang-platform-kit, chuang-sdlc and chuang-alerts in the same repository
(app-pipeline/, tags app-v<version>), standard library only, pinned only where an app's pipeline runs."""
__version__ = '0.5.0'
