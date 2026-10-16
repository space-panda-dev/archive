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

      # Door and the archive command. Building runs `cabal test all`.
      archiveFor = pkgs: pkgs.haskell.lib.overrideCabal
        (pkgs.haskell.lib.addBuildTools
          (pkgs.haskellPackages.callCabal2nix "archive" self { })
          [ pkgs.git pkgs.git-annex pkgs.python3 pkgs.perl pkgs.which ])
        (old: {
          preCheck = ''
            export PATH="$PWD/dist/build/archive:$PWD/dist/build/door:$PATH"
          '' + gitIdentity;
        });

      # World, the relay, enrichment, and their tests. pyshacl and owlrl are not in
      # nixpkgs; these are the newest releases that accept its rdflib.
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
        in [ ps.pyoxigraph ps.rdflib ps.jsonschema pyshacl ps.numpy ]);

      # The Python parts and commands for World, the relay, enrichment, and
      # the fixture. The relay reads enrich/derived.py, the derived-media layout.
      toolsFor = pkgs:
        let
          archive = archiveFor pkgs;
          python = pythonFor pkgs;
        in pkgs.runCommand "archive-tools" { nativeBuildInputs = [ pkgs.makeWrapper ]; } ''
          mkdir -p $out/share/archive $out/bin
          cp -r ${self}/world ${self}/relay ${self}/enrich ${self}/fixture ${self}/spec ${self}/LICENSES $out/share/archive/
          chmod -R u+w $out/share/archive
          rm -f $out/share/archive/fixture/test_*.py
          rm -rf $out/share/archive/world/tests $out/share/archive/relay/tests $out/share/archive/enrich/tests
          wrap() {
            makeWrapper ${python}/bin/python3 $out/bin/$1 \
              --add-flags $out/share/archive/$2 \
              --prefix PATH : ${pkgs.lib.makeBinPath [ archive pkgs.git pkgs.git-annex pkgs.ffmpeg-headless ]} \
              --set-default ARCHIVE_GIT_EXECUTABLE ${pkgs.git}/bin/git \
              --set-default ARCHIVE_GIT_ANNEX_EXECUTABLE ${pkgs.git-annex}/bin/git-annex
          }
          wrap world world/world.py
          wrap world-conformance world/conformance.py
          wrap relay relay/relay.py
          wrap enrich-media enrich/media.py
          wrap archive-fixture fixture/build.py
          wrap archive-fixture-serve fixture/serve.py
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
        in
        {
          inherit archive;

          # Every Python suite, against the Door and Archive built above;
          # a missing binary fails rather than skips.
          python = pkgs.runCommand "archive-python-tests"
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
            python -m unittest discover -s world/tests
            python -m unittest discover -s relay/tests
            python -m unittest discover -s enrich/tests
            python -m unittest discover -s shadow -p 'test_*.py'
            python -m unittest discover -s migration -p 'test_*.py'
            python -m unittest discover -s fixture -p 'test_*.py'
            touch $out
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
