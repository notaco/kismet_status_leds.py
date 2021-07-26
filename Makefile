# Plugin name
PLUGIN_NAME ?= statusleds

# You will need kismet git-master sources if not compiling in the main tree
KIS_SRC_DIR ?= /usr/src/kismet
KIS_INC_DIR ?= $(KIS_SRC_DIR)

ifneq (,$(wildcard $(KIS_SRC_DIR)/Makefile.inc))
        include $(KIS_SRC_DIR)/Makefile.inc
else
        INSTALL ?= $(shell which install)
        INSTUSR ?= root
        INSTGRP ?= root
        nosourcedir := 1
endif

BLDHOME	= .
top_builddir = $(BLDHOME)

plugindir ?= $(shell pkg-config --variable=plugindir kismet)
ifeq ("$(plugindir)", "")
	plugindir := "/usr/local/lib/kismet/"
	plugindirgeneric := 1
endif

BIN ?= $(shell pkg-config --variable=exec_prefix kismet)
ifeq ("$(BIN)", "")
	BIN := "/usr/local/bin/"
	bindirgeneric := 1
endif

all:
	@-echo "Run 'make install' to install the plugin and helper."

install:
ifeq ("$(INSTALL)", "")
	$(error "No install found in kismet source include file or path!")
endif
ifeq ("$(nosourcedir)", "1")
	@echo "No source directory found, assuming package manger install."
	@echo "If INSTUSR or INSTGRP werent't provided they were set to root"
endif
ifeq ("$(plugindirgeneric)", "1")
	@echo "No kismet install found in pkgconfig, assuming plugins install to /usr/local/lib"
endif
ifeq ("$(bindirgeneric)", "1")
	@echo "No kismet install found in pkgconfig, assuming execs install to /usr/local/bin"
endif

	mkdir -p $(DESTDIR)/$(plugindir)/$(PLUGIN_NAME)
	$(INSTALL) -o $(INSTUSR) -g $(INSTGRP) -m 444 manifest.conf $(DESTDIR)/$(plugindir)/$(PLUGIN_NAME)/manifest.conf
	$(INSTALL) -o $(INSTUSR) -g $(INSTGRP) -m 555 kismet_status_leds.py $(BIN)/kismet_status_leds.py;


userinstall:
	mkdir -p ${HOME}/.kismet/plugins/$(PLUGIN_NAME)
	$(INSTALL) manifest.conf ${HOME}/.kismet/plugins/$(PLUGIN_NAME)/manifest.conf
	$(INSTALL) -o $(INSTUSR) -g $(INSTGRP) -m 555 kismet_status_leds.py $(BIN)/kismet_status_leds.py;

