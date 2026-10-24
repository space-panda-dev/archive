{
  description = "Reproducible development, checks, and releases for Archive";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";

  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-darwin" "x86_64-darwin" "aarch64-linux" "x86_64-linux" ];
      linuxSystems = [ "aarch64-linux" "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      forLinux = nixpkgs.lib.genAttrs linuxSystems;

      gitIdentity = ''
        export GIT_AUTHOR_NAME="Archive tests"
        export GIT_AUTHOR_EMAIL="archive-tests@example.invalid"
        export GIT_COMMITTER_NAME="Archive tests"
        export GIT_COMMITTER_EMAIL="archive-tests@example.invalid"
      '';

      # What the Haskell package reads: its Cabal files, its sources and
      # tests, the specification (its data files, which the tests also
      # read), and the fixture's plan, which a Door test checks. A change anywhere else leaves the build, and CI's cached copy
      # of it, unchanged (ADR 0117).
      haskellSource = nixpkgs.lib.fileset.toSource {
        root = ./.;
        fileset = nixpkgs.lib.fileset.unions [ ./archive.cabal ./cabal.project ./door ./archive ./spec ./fixture/plan.json ];
      };

      # Door and the archive command. Building runs `cabal test all`.
      archiveFor = pkgs: pkgs.haskell.lib.overrideCabal
        (pkgs.haskell.lib.addBuildTools
          (pkgs.haskellPackages.callCabal2nix "archive" haskellSource { })
          [ pkgs.git pkgs.git-annex pkgs.python3 pkgs.perl pkgs.which ])
        (old: {
          preCheck = ''
            export PATH="$PWD/dist/build/archive:$PWD/dist/build/door:$PATH"
          '' + gitIdentity;
        });

      # World, the relay, enrich, capture, and their tests. pyshacl and owlrl
      # are not in nixpkgs; these are the newest releases that accept its rdflib.
      # numpy is the span detector's, the work matcher's, and derived
      # media's; scipy the matcher's (enrich/requirements.txt).
      pythonFor = pkgs: pkgs.python3.withPackages (ps:
        let
          wheel = { pname, version, sha256, dependencies }: ps.buildPythonPackage {
            inherit pname version dependencies;
            format = "wheel";
            src = pkgs.fetchurl {
              url = "https://files.pythonhosted.org/packages/py3/${builtins.substring 0 1 pname}/${pname}/${pname}-${version}-py3-none-any.whl";
              inherit sha256;
            };
            # rdflib's optional html extra is not packaged and is not used.
            dontCheckRuntimeDeps = true;
          };
          owlrl = wheel {
            pname = "owlrl"; version = "7.1.3";
            sha256 = "371076ed3427b7962bf5675aded0bb2d23bee61595b85582f8dce11f75ee10a8";
            dependencies = [ ps.rdflib ];
          };
          pyshacl = wheel {
            pname = "pyshacl"; version = "0.30.1";
            sha256 = "d7e0c21b25e948bb643dbc5db6258da64a90a8ac89055c1fe562b469031072aa";
            dependencies = [ ps.rdflib owlrl ps.packaging ps.prettytable ];
          };
        in [ ps.pyoxigraph ps.rdflib ps.jsonschema pyshacl ps.numpy ps.scipy ]);

      # The matcher's interpreter (ADR 0071): CPython 3.14 with Essentia
      # 2.1b6.dev1438, the build the V2 system calibrated its thresholds with,
      # numpy, scipy, and pyoxigraph (the enrich tests read their claims back
      # through World), all from the wheels enrich/requirements-match.txt
      # pins. That Essentia release ships only CPython 3.14 wheels, and only
      # for these systems; elsewhere enrich and capture run on pythonFor and
      # the window matcher refuses to run. nixpkgs does not cache its 3.14
      # packages, so they are the published wheels too.
      matchWheels = let
        pypi = path: name: "https://files.pythonhosted.org/packages/${path}/${name}";
      in {
        x86_64-linux = {
          pyoxigraph = { version = "0.5.2"; sha256 = "f52e7e47ff11b7b9510a6810de132c1613aa543ad6305d95de3fcb93b4c1f6ee";
            url = pypi "ad/f7/a0e3df6b19f9a20d37ac0d75a7bc99ce4e53894a2e7d584b4e56df5b7f05" "pyoxigraph-0.5.2-cp38-abi3-manylinux_2_28_x86_64.whl"; };
          essentia = { version = "2.1b6.dev1438"; sha256 = "1ee6107fe63fb3b50f8aa68466951f6ca1e162a700d8deffd46ab0506dab9acb";
            url = pypi "d9/dc/feafca6d0989e40a324b5e977054ec1eb7a7d93260e023b4e5c5071202c7" "essentia-2.1b6.dev1438-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"; };
          numpy = { version = "2.5.4"; sha256 = "d549420b8858885cea8838a727842249218b9c1da24dd517e25c9c7a948310a3";
            url = pypi "16/d5/928078d2b28f26829b138b4a6c3980045022fb409f570657a224ae60ef4e" "numpy-2.5.4-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"; };
          scipy = { version = "1.18.1"; sha256 = "ac0333bdf38309aa3dcbe7e3fa7ea29e7a2c37c6ea306a757b700ded8e4596ad";
            url = pypi "6b/89/2a844506d49651e9aa1af6ef95b6bd8031cb1d5a4375edec6155037e04cf" "scipy-1.18.1-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"; };
          pyyaml = { version = "6.0.3"; sha256 = "c458b6d084f9b935061bc36216e8a69a7e293a2f1e68bf956dcd9e6cbcd143f5";
            url = pypi "88/f9/16491d7ed2a919954993e48aa941b200f38040928474c9e85ea9e64222c3" "pyyaml-6.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl"; };
        };
        aarch64-darwin = {
          pyoxigraph = { version = "0.5.2"; sha256 = "1f869c77720e8628a35c9ea87c0254e81eab91ff8a61df1cd9eb82c9258a53c3";
            url = pypi "12/c5/a7fef153e9e86e80f2ed688b04a73ca5e17b41fef8dea08dd892e7201289" "pyoxigraph-0.5.2-cp38-abi3-macosx_11_0_arm64.whl"; };
          essentia = { version = "2.1b6.dev1438"; sha256 = "3a4a51f7b59d76b9beb9a1aacdaa9b9c0de89f14b02ab45050f86230241044c5";
            url = pypi "5c/b3/26c54a8fd03b0378574767e4e9fda3ad41f68dba95649e346e9895d236bd" "essentia-2.1b6.dev1438-cp314-cp314-macosx_15_0_arm64.whl"; };
          numpy = { version = "2.5.4"; sha256 = "956555e0603a4d38019ae6925711cb9dc43195c076a928accf7ea5d50bddfe53";
            url = pypi "d5/b2/c6ce165acffceb15a82c07b9cc77d391f86b3f379ba62911908ae5d34b91" "numpy-2.5.4-cp314-cp314-macosx_14_0_arm64.whl"; };
          scipy = { version = "1.18.1"; sha256 = "011413b7426b75012840e35649e00fe0a2c3bae89fed433876e3a99251572efc";
            url = pypi "70/e8/6b0c288c50942d78193696c9f15f9a0874f5178aa0ddf40f83d9924b3e8d" "scipy-1.18.1-cp314-cp314-macosx_14_0_arm64.whl"; };
          pyyaml = { version = "6.0.3"; sha256 = "34d5fcd24b8445fadc33f9cf348c1047101756fd760b4dacb5c3e99755703310";
            url = pypi "bd/9c/4d95bb87eb2063d20db7b60faa3840c1b18025517ae857371c4dd55a6b3a" "pyyaml-6.0.3-cp314-cp314-macosx_11_0_arm64.whl"; };
        };
        x86_64-darwin = {
          pyoxigraph = { version = "0.5.2"; sha256 = "922a75718d50e1a6621c63b33e4268b83dae467fbb6949f1ee0d36abb1728f57";
            url = pypi "15/52/f4bfdb7edf140836f2cbca93e2fa20e3b69b8367b5923d16e612bb8b0772" "pyoxigraph-0.5.2-cp38-abi3-macosx_10_14_x86_64.whl"; };
          essentia = { version = "2.1b6.dev1438"; sha256 = "b387e374008ab3e8f72fbc93d0c91c7fde97da71d26598f19a84750ff870582b";
            url = pypi "af/eb/e823082c6e9a048597c4dfc4aecc8dde4becf99050c78cdbe117e500ec33" "essentia-2.1b6.dev1438-cp314-cp314-macosx_15_0_x86_64.whl"; };
          numpy = { version = "2.5.4"; sha256 = "2c2c4afffdeb7920e445028dd71eb932cac3e704792e964bc2a232426d4f1255";
            url = pypi "77/7f/dd85ce260a669a89be06842cf355d7353a33e6cfbc590fb8ebb947d88dc9" "numpy-2.5.4-cp314-cp314-macosx_14_0_x86_64.whl"; };
          scipy = { version = "1.18.1"; sha256 = "88f0e784020649f88ea48c9f5ddfa403bf9205820667c0914740b392035afb82";
            url = pypi "4b/e0/54fd3793c729e3b936782f181b59cbb1205bf250ab605a16cb1ba61cdd5e" "scipy-1.18.1-cp314-cp314-macosx_14_0_x86_64.whl"; };
          pyyaml = { version = "6.0.3"; sha256 = "8d1fab6bb153a416f9aeb4b8763bc0f22a5586065f86f7664fc23339fc1c1fac";
            url = pypi "9d/8c/f4bd7f6465179953d3ac9bc44ac1a8a3e6122cf8ada906b4f96c60172d43" "pyyaml-6.0.3-cp314-cp314-macosx_10_13_x86_64.whl"; };
        };
      };
      hasMatcher = system: builtins.hasAttr system matchWheels;
      matchPythonFor = pkgs:
        let
          python = pkgs.python314;
          wheels = matchWheels.${pkgs.stdenv.hostPlatform.system};
          isLinux = pkgs.stdenv.hostPlatform.isLinux;
          wheel = pname: dependencies: python.pkgs.buildPythonPackage {
            inherit pname dependencies;
            inherit (wheels.${pname}) version;
            format = "wheel";
            src = pkgs.fetchurl { inherit (wheels.${pname}) url sha256; };
            nativeBuildInputs = pkgs.lib.optionals isLinux [ pkgs.autoPatchelfHook ];
            buildInputs = pkgs.lib.optionals isLinux [ pkgs.stdenv.cc.cc.lib pkgs.zlib ];
          };
          six = python.pkgs.buildPythonPackage {
            pname = "six"; version = "1.17.0"; format = "wheel";
            src = pkgs.fetchurl {
              url = "https://files.pythonhosted.org/packages/b7/ce/149a00dd41f10bc29e5921b496af8b574d8413afcd5e30dfa0ed46c2cc5e/six-1.17.0-py2.py3-none-any.whl";
              sha256 = "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274";
            };
          };
          numpy = wheel "numpy" [ ];
          scipy = wheel "scipy" [ numpy ];
          pyyaml = wheel "pyyaml" [ ];
          essentia = wheel "essentia" [ numpy pyyaml six ];
          pyoxigraph = wheel "pyoxigraph" [ ];
        in python.withPackages (_: [ numpy scipy essentia pyoxigraph ]);
      # The interpreter enrich and capture run on.
      enrichPythonFor = pkgs:
        if hasMatcher pkgs.stdenv.hostPlatform.system then matchPythonFor pkgs else pythonFor pkgs;

      # The Python parts and commands for World, the relay, enrichment, and
      # the fixture. The relay reads enrich/derived.py, the derived-media layout,
      # and reports ARCHIVE_COMMIT and ARCHIVE_BUILD on /health (ADR 0114).
      toolsFor = pkgs:
        let
          archive = archiveFor pkgs;
          python = pythonFor pkgs;
          enrichPython = enrichPythonFor pkgs;
        in pkgs.runCommand "archive-tools" { nativeBuildInputs = [ pkgs.makeWrapper ]; } ''
          mkdir -p $out/share/archive $out/bin
          cp -r ${self}/world ${self}/relay ${self}/fixture ${self}/enrich ${self}/capture ${self}/spec ${self}/LICENSES $out/share/archive/
          chmod -R u+w $out/share/archive
          rm -f $out/share/archive/fixture/test_*.py $out/share/archive/enrich/test_*.py $out/share/archive/enrich/support.py \
            $out/share/archive/capture/test_*.py
          rm -rf $out/share/archive/world/tests $out/share/archive/relay/tests $out/share/archive/enrich/tests
          wrap() {
            makeWrapper $1/bin/python3 $out/bin/$2 \
              --add-flags $out/share/archive/$3 \
              --prefix PATH : ${pkgs.lib.makeBinPath [ archive pkgs.git pkgs.git-annex pkgs.ffmpeg-headless ]} \
              --set-default ARCHIVE_GIT_EXECUTABLE ${pkgs.git}/bin/git \
              --set-default ARCHIVE_GIT_ANNEX_EXECUTABLE ${pkgs.git-annex}/bin/git-annex \
              --set-default ARCHIVE_COMMIT "${self.rev or self.dirtyRev or ""}" \
              --set-default ARCHIVE_BUILD "$(basename $out)"
          }
          wrap ${python} world world/world.py
          wrap ${python} world-conformance world/conformance.py
          wrap ${python} relay relay/relay.py
          wrap ${python} archive-fixture fixture/build.py
          wrap ${python} archive-fixture-serve fixture/serve.py
          # The capture import runs enrich and media with its own interpreter.
          wrap ${enrichPython} enrich-media enrich/media.py
          wrap ${enrichPython} enrich enrich/enrich.py
          wrap ${enrichPython} capture capture/capture.py
          wrap ${enrichPython} capture-send capture/send.py
        '';
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          archive = archiveFor pkgs;
          tools = toolsFor pkgs;
        in
        {
          default = archive;
          inherit archive tools;
          # The interpreter enrich and capture run on (matchPythonFor where
          # an Essentia wheel exists).
          enrich-python = enrichPythonFor pkgs;

          # What a client builds against, published with each release
          # (ADR 0062): the specification's catalogs and bundles, World's
          # projection descriptions, Door's and the relay's descriptions, and
          # the fixture builder.
          bundle = pkgs.runCommand "archive-release-bundle" { nativeBuildInputs = [ pkgs.gnutar pkgs.gzip ]; } ''
            root=$out/archive-release-bundle
            mkdir -p $root/door $root/relay $root/world
            cp -r ${self}/spec $root/spec
            cp -r ${self}/world/projection $root/world/projection
            cp ${self}/door/service.schema.json $root/door/
            cp ${self}/relay/openapi.json $root/relay/
            cp -r ${self}/fixture $root/fixture
            cp -r ${self}/LICENSES ${self}/REUSE.toml $root/
            chmod -R u+w $root
            rm -f $root/fixture/test_*.py
            (cd $out && tar --sort=name --mtime=@1 --owner=0 --group=0 --numeric-owner -czf archive-release-bundle.tar.gz archive-release-bundle)
          '';
        } // nixpkgs.lib.optionalAttrs (builtins.elem system linuxSystems) {
          # Door, World, the relay, and the fixture builder in one image, so
          # a client's CI needs no tailnet and no one's archive (ADR 0062).
          image = pkgs.dockerTools.buildLayeredImage {
            name = "archive";
            tag = "latest";
            contents = [ archive tools pkgs.git pkgs.git-annex pkgs.coreutils pkgs.bashInteractive pkgs.cacert ];
            extraCommands = ''
              mkdir -p tmp work
              chmod 1777 tmp
            '';
            config = {
              Env = [
                "ARCHIVE_GIT_EXECUTABLE=${pkgs.git}/bin/git"
                "ARCHIVE_GIT_ANNEX_EXECUTABLE=${pkgs.git-annex}/bin/git-annex"
                "GIT_AUTHOR_NAME=Archive fixture"
                "GIT_AUTHOR_EMAIL=fixture@example.invalid"
                "GIT_COMMITTER_NAME=Archive fixture"
                "GIT_COMMITTER_EMAIL=fixture@example.invalid"
                "HOME=/tmp"
              ];
              WorkingDir = "/work";
              Cmd = [ "archive-fixture-serve" "--work" "/work/fixture" "--port" "8450" ];
            };
          };
        });

      checks = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          archive = archiveFor pkgs;
          python = pythonFor pkgs;
          enrichPython = enrichPythonFor pkgs;
          # Each Python suite against the Door and Archive built above; a
          # missing binary fails rather than skips. Three checks, so CI runs
          # them side by side (ADR 0117).
          suite = name: script: pkgs.runCommand "archive-${name}-tests"
            { nativeBuildInputs = [ python archive pkgs.git pkgs.git-annex pkgs.ffmpeg-headless ]; } ''
            cp -r ${self} source
            chmod -R u+w source
            cd source
            export HOME=$TMPDIR
            ${gitIdentity}
            export ARCHIVE_TEST_DOOR=${archive}/bin/door ARCHIVE_TEST_ARCHIVE=${archive}/bin/archive
            export ARCHIVE_REQUIRE_INTEGRATION=1
            export ARCHIVE_GIT_EXECUTABLE=${pkgs.git}/bin/git
            export ARCHIVE_GIT_ANNEX_EXECUTABLE=${pkgs.git-annex}/bin/git-annex
            ${script}
            touch $out
          '';
          # enrich and capture run on their own interpreter, which must hold
          # the pinned Essentia build wherever one exists.
          requireEssentia = pkgs.lib.optionalString (hasMatcher system) "export ARCHIVE_REQUIRE_ESSENTIA=1";
        in
        {
          inherit archive;

          python = suite "python" ''
            python -m unittest discover -s world/tests
            python -m unittest discover -s relay/tests
            python -m unittest discover -s enrich/tests
            python -m unittest discover -s shadow -p 'test_*.py'
            python -m unittest discover -s migration -p 'test_*.py'
            python -m unittest discover -s fixture -p 'test_*.py'
          '';
          enrich = suite "enrich" ''
            ${requireEssentia}
            ${enrichPython}/bin/python3 -m unittest discover -s enrich -p 'test_*.py'
          '';
          capture = suite "capture" ''
            ${requireEssentia}
            ${enrichPython}/bin/python3 -m unittest discover -s capture -p 'test_*.py'
          '';
        });

      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          haskell = pkgs.haskellPackages;
        in
        {
          default = pkgs.mkShell {
            packages = [
              haskell.cabal-install
              haskell.ghc
              pkgs.git
              pkgs.git-annex
              pkgs.ffmpeg-headless
              pkgs.perl
              (pythonFor pkgs)
              pkgs.which
            ];
          };
        });
    };
}
