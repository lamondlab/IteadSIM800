# IteadSIM800
A Python 3 library for driving the [Itead RPI SIM800 GSM/GPRS Add-On V2.0](https://www.itead.cc/wiki/RPI_SIM800_GSM/GPRS_ADD-ON_V2.0)
with the Raspberry Pi.

## Hardware
If you are using a Raspberry Pi Model 1 A+/B+, Model 2/3 B or Zero then this add-on board will connect directly to the 40-pin GPIO.
If you are using an Model 1 A/B with a 28-pin GPIO connector then you will have to connect the board to the Pi with ribbon cable
or dupont connectors as the composite video and audio connectors are in the way. Only the follow seven pins need to be connected:

| Pin | Function |
| --- | --- |
| 1   | 3.3V |
| 2 | 5V |
| 6 | GND |
| 8 | TXD |
| 10 | RXD |
| 11 | SIM800 Power |
| 12 | SIM800 Reset |

## Software
This library has been tested with Python 3.4.2 running on a Raspberry Pi Model B (Rev.2) with Raspbian Jessie Lite (2016-05-27).
It depends on PySerial and  Ben Croston's RPi.GPIO which can be installed (if not already) as follows:

```
$ sudo apt-get update
$ sudo apt-get install python3-rpi.gpio
$ sudo pip3 install pyserial
```

The file `sms.py` is both the library and a working example if read/executed:

```
$ sudo python3 sms.py
```

Most methods also contain (brief) comments as to there function.

This setup has been tested in the UK using a SIM card from [giffgaff](https://www.giffgaff.com/) with whom we have no relationship,
personal or otherwise.
