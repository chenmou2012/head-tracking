# HeadTrack 插件 —— 生成 universal binary (arm64 + x86_64)
#
#   make            编译到 build/HeadTrack/mac_x64/HeadTrack.xpl
#   make install    复制到 $(XPLANE_DIR)/Resources/plugins/HeadTrack/mac_x64/
#   make clean
#
# X-Plane SDK 解压后放到 plugin/SDK（应包含 SDK/CHeaders 和 SDK/Libraries/Mac），
# 或用 make SDK=/path/to/SDK 指定。

SDK        ?= SDK
XPLANE_DIR ?= $(HOME)/Library/Application Support/Steam/steamapps/common/X-Plane 12

ARCHS   := -arch arm64 -arch x86_64
DEFS    := -DAPL=1 -DIBM=0 -DLIN=0 -DXPLM200 -DXPLM210 -DXPLM300 -DXPLM301 -DXPLM303 -DXPLM400
CFLAGS  := $(ARCHS) -std=c11 -O2 -Wall -Wextra -fvisibility=hidden -mmacosx-version-min=11.0 \
           $(DEFS) -I$(SDK)/CHeaders/XPLM
LDFLAGS := $(ARCHS) -dynamiclib -mmacosx-version-min=11.0 \
           -F$(SDK)/Libraries/Mac -framework XPLM

OUT := build/HeadTrack/mac_x64/HeadTrack.xpl
SRC := src/headtrack.c
HDR := src/coords.h

.PHONY: all install clean check-sdk

all: check-sdk $(OUT)

check-sdk:
	@test -f "$(SDK)/CHeaders/XPLM/XPLMPlugin.h" || \
	  (echo "找不到 X-Plane SDK：请把 SDK 解压到 plugin/SDK，或 make SDK=/path/to/SDK"; exit 1)

$(OUT): $(SRC) $(HDR)
	@mkdir -p $(dir $@)
	clang $(CFLAGS) $(LDFLAGS) -o $@ $(SRC)
	@lipo -info $@

install: all
	@test -d "$(XPLANE_DIR)/Resources/plugins" || (echo "找不到 X-Plane：$(XPLANE_DIR)"; exit 1)
	mkdir -p "$(XPLANE_DIR)/Resources/plugins/HeadTrack/mac_x64"
	@# 先删除再复制：X-Plane 运行中时，原地覆盖已加载的库可能导致崩溃
	rm -f "$(XPLANE_DIR)/Resources/plugins/HeadTrack/mac_x64/HeadTrack.xpl"
	cp $(OUT) "$(XPLANE_DIR)/Resources/plugins/HeadTrack/mac_x64/HeadTrack.xpl"
	@echo "已安装到 $(XPLANE_DIR)/Resources/plugins/HeadTrack/mac_x64/HeadTrack.xpl"

clean:
	rm -rf build
