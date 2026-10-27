# Evaluate SDK selectors for every Unix target without building foreign tools.
# This runs from the lint environment, before the full SDK can be installed.
{ configFile ? ../devbox.json }:
let
  config = builtins.fromJSON (builtins.readFile configFile);
  source = "github:NixOS/nixpkgs/${config.nixpkgs.commit}";
  flake = builtins.getFlake source;
  systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
  inspect = system: reference:
    let
      parts = flake.lib.splitString "#" reference;
      selector = flake.lib.splitString "^" (builtins.elemAt parts 1);
      attribute = builtins.head selector;
      package = flake.lib.getAttrFromPath (flake.lib.splitString "." attribute) flake.legacyPackages.${system};
      requested = if builtins.length selector == 2
        then flake.lib.splitString "," (builtins.elemAt selector 1)
        else [ (package.outputName or "out") ];
    in
    if builtins.length parts != 2 || builtins.head parts != source || builtins.length selector > 2
      then throw "SDK dependency must use the pinned Nixpkgs source: ${reference}"
      else {
        inherit requested;
        available = package.outputs;
        missing = builtins.filter (name: !(builtins.elem name package.outputs)) requested;
      };
  audit = builtins.listToAttrs (map (system: {
    name = system;
    value = builtins.listToAttrs (map (reference: {
      name = reference;
      value = inspect system reference;
    }) (builtins.filter (reference:
      !(config.packages.${reference} ? platforms)
      || builtins.elem system config.packages.${reference}.platforms
    ) (builtins.attrNames config.packages)));
  }) systems);
  failures = builtins.concatLists (map (system:
    builtins.concatLists (map (reference:
      let result = audit.${system}.${reference}; in
      if result.missing == [] then [] else [{
        inherit system reference;
        inherit (result) requested available missing;
      }]
    ) (builtins.attrNames audit.${system}))
  ) systems);
in
if failures == [] then audit
else throw "Invalid SDK outputs: ${builtins.toJSON failures}"
