# native-gui -- build on Debian 13 (see deps-debian.txt for the packages).
#   make            both binaries
#   make smoke      build + run the Hebrew bidi smoke test (writes hebrew-bidi.png)
#   make png        render every card, both themes, to ./out/*.png (sample data;
#                   STATE=/path/to/state.txt renders the real thing)
CC      ?= cc
CFLAGS  ?= -O2 -g -Wall -Wextra -std=c11
CFLAGS  += -D_GNU_SOURCE

GUI_PKGS   = pangocairo cairo libdrm
SMOKE_PKGS = pangocairo cairo harfbuzz fribidi

GUI_SRC = src/main.c src/screens.c src/screens_capture.c src/screens_rounds.c src/widgets.c \
          src/state.c src/text.c src/draw.c src/theme.c src/backend.c src/input.c
GUI_HDR = src/ui.h src/widgets.h src/state.h src/text.h src/draw.h src/theme.h src/backend.h src/input.h

all: imagectl-station-gui hebrew-bidi-smoke

imagectl-station-gui: $(GUI_SRC) $(GUI_HDR)
	$(CC) $(CFLAGS) $$(pkg-config --cflags $(GUI_PKGS)) -o $@ $(GUI_SRC) $$(pkg-config --libs $(GUI_PKGS)) -lm

hebrew-bidi-smoke: smoke/hebrew_bidi.c
	$(CC) $(CFLAGS) $$(pkg-config --cflags $(SMOKE_PKGS)) -o $@ $< $$(pkg-config --libs $(SMOKE_PKGS)) -lm

smoke: hebrew-bidi-smoke
	./hebrew-bidi-smoke hebrew-bidi.png

png: imagectl-station-gui
	mkdir -p out
	./imagectl-station-gui --png out/station --mac 3C:52:82:A1:00:1F --ip 10.10.10.31 $(if $(STATE),--state $(STATE),)

clean:
	rm -f imagectl-station-gui hebrew-bidi-smoke hebrew-bidi.png
	rm -rf out

.PHONY: all smoke png clean
