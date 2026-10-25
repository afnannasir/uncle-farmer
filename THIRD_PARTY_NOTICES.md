# Third-party notices

## NVDA Controller Client

`nvdaControllerClient64.dll` is the NVDA Controller Client by NV Access Limited, part of NVDA
(https://www.nvaccess.org/, source at https://github.com/nvaccess/nvda). It lets Uncle Farmer speak
through NVDA when NVDA is running. It is distributed under the GNU Lesser General Public License,
version 2.1. Uncle Farmer loads it at run time and does not modify it; you may replace it with any
compatible build of the same library.

## wxPython

The windows are built with wxPython (https://wxpython.org/), under the wxWindows Library Licence.
Release builds made with PyInstaller include it.

## orjson

Saving and reading JSON uses orjson (https://github.com/ijl/orjson), Copyright (c) 2018 ijl,
offered under either the Apache License 2.0 or the MIT License. Release builds made with PyInstaller
include it.

## pydantic

Checking that settings, profiles and plans have the right shape uses pydantic
(https://github.com/pydantic/pydantic) and pydantic-core, Copyright (c) 2017-present Samuel Colvin
and contributors, under the MIT License. Release builds made with PyInstaller include them.
