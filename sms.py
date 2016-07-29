from serial import Serial
import RPi.GPIO as IO, atexit, logging, sys
from time import sleep
from enum import IntEnum

PORT="/dev/ttyAMA0"
BAUD=9600
GSM_ON=11
GSM_RESET=12

class ATResp(IntEnum):
    ErrorNoResponse=-1
    ErrorDifferentResponse=0
    OK=1

class SMSMeesageFormat(IntEnum):
    PDU=0
    Text=1

class RSSI(IntEnum):
    """
    Received Signal Strength Indication as 'bars'.
    Interpretted form AT+CSQ return value as follows:

    ZeroBars: Return value=99 (unknown or not detectable)
    OneBar: Return value=0 (-115dBm or less)
    TwoBars: Return value=1 (-111dBm)
    ThreeBars: Return value=2...30 (-110 to -54dBm)
    FourBars: Return value=31 (-52dBm or greater)
    """

    ZeroBars=0
    OneBar=1
    TwoBars=2
    ThreeBars=3
    FourBars=4

    @classmethod
    def fromCSQ(cls, csq):
        csq=int(csq)
        if csq==99: return cls.ZeroBars
        elif csq==0: return cls.OneBar
        elif csq==1: return cls.TwoBars
        elif 2<=csq<=30: return cls.ThreeBars
        elif csq==31: return cls.FourBars

class NetworkStatus(IntEnum):
    NotRegistered=0
    RegisteredHome=1
    Searching=2
    Denied=3
    Unknown=4
    RegisteredRoaming=5

@atexit.register
def cleanup(): IO.cleanup()

class SMS(object):
    def __init__(self, port, baud, loglevel=logging.WARNING):
        self._port=port
        self._baud=baud

        self._ready=False
        self._serial=None

        self._logger=logging.getLogger("SMS")
        handler=logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s : %(levelname)s -> %(message)s"))
        self._logger.addHandler(handler)
        self._logger.setLevel(loglevel)

    def setup(self):
        self._logger.debug("Setup")
        IO.setmode(IO.BOARD)
        IO.setup(GSM_ON, IO.OUT, initial=IO.LOW)
        IO.setup(GSM_RESET, IO.OUT, initial=IO.LOW)
        self._serial=Serial(self._port, self._baud)

    def reset(self):
        self._logger.debug("Reset (duration ~6.2s)")
        IO.output(GSM_ON, IO.HIGH)
        sleep(1.2)
        IO.output(GSM_ON, IO.LOW)
        sleep(5.)

    def sendATCmdWaitResp(self, cmd, response, timeout=.5, interByteTimeout=.1, attempts=1):
        """
        This function is designed to check for simple one line responses, i.e. 'OK'
        """
        self._logger.debug("Send AT Command: {}".format(cmd))
        self._serial.timeout=timeout
        self._serial.inter_byte_timeout=interByteTimeout

        status=ATResp.ErrorNoResponse
        for i in range(attempts):
            self._logger.debug("Attempt {}".format(i+1))
            self._serial.write(cmd.encode('utf-8')+b'\r')
            self._serial.flush()

            lines=self._serial.readlines()
            lines=[l.decode('utf-8').strip() for l in lines]
            lines=[l for l in lines if len(l) and not l.isspace()]
            self._logger.debug("Lines: {}".format(lines))
            if len(lines)<1: continue
            line=lines[-1]
            self._logger.debug("Line: {}".format(line))

            if not len(line) or line.isspace(): continue
            elif line==response: return ATResp.OK            
            else: return ATResp.ErrorDifferentResponse
        return status

    def sendATCmdWaitReturnResp(self, cmd, response, timeout=.5, interByteTimeout=.1):
        """
        This function is designed to return data and check for a final response, i.e. 'OK'
        """        
        self._logger.debug("Send AT Command: {}".format(cmd))
        self._serial.timeout=timeout
        self._serial.inter_byte_timeout=interByteTimeout

        self._serial.write(cmd.encode('utf-8')+b'\r')
        self._serial.flush()
        lines=self._serial.readlines()
        lines=[l.decode('utf-8').strip() for l in lines]
        lines=[l for l in lines if len(l) and not l.isspace()]        
        self._logger.debug("Lines: {}".format(lines))

        if not len(lines): return (ATResp.ErrorNoResponse, None)

        _response=lines.pop(-1)
        self._logger.debug("Response: {}".format(_response))
        if not len(_response) or _response.isspace(): return (ATResp.ErrorNoResponse, None)
        elif response==_response: return (ATResp.OK, lines)
        return (ATResp.ErrorDifferentResponse, None)

    def parseReply(self, data, beginning, divider=',', index=0):
        self._logger.debug("Parse Reply: {}, {}, {}, {}".format(data, beginning, divider, index))
        if not data.startswith(beginning): return False, None
        data=data.replace(beginning,"")
        data=data.split(divider)
        try: return True,data[index]
        except IndexError: return False, None

    def getSingleResponse(self, cmd, response, beginning, divider=",", index=0):
        status,data=self.sendATCmdWaitReturnResp(cmd,response)
        if status!=ATResp.OK: return None
        if len(data)!=1: return None
        ok,data=self.parseReply(data[0], beginning, divider, index)
        if not ok: return None
        return data

    def turnOn(self):
        self._logger.debug("Turn On")
        for i in range(2):
            status=self.sendATCmdWaitResp("AT", "OK", attempts=5)
            if status==ATResp.OK:
                self._logger.debug("GSM module ready.")
                self._ready=True
                return True
            elif status==ATResp.ErrorDifferentResponse:
                self._logger.debug("GSM module returned invalid response, check baud rate?")
            elif i==0:
                self._logger.debug("GSM module is not responding, resetting...")
                self.reset()
            else: self._logger.error("GSM module failed to respond after reset!")
        return False

    def setEchoOff(self):
        self._logger.debug("Set Echo Off")
        self.sendATCmdWaitResp("ATE0", "OK")
        status=self.sendATCmdWaitResp("ATE0", "OK")
        return status==ATResp.OK

    def getIMEI(self):
        self._logger.debug("Get Internation Mobile Equipment Identity (IMEI)")
        status,imei=self.sendATCmdWaitReturnResp("AT+GSN","OK")
        if status==ATResp.OK and len(imei)==1: return imei[0]
        return None

    def getSIMCCID(self):
        self._logger.debug("Get SIM Integrated Circuit Card Identifier (ICCID)")
        status,ccid=self.sendATCmdWaitReturnResp("AT+CCID","OK")
        if status==ATResp.OK and len(ccid)==1: return ccid[0]
        return None        

    def getNetworkStatus(self):
        self._logger.debug("Get Network Status")
        status=self.getSingleResponse("AT+CREG?","OK","+CREG: ",index=1)
        if status is None: return status
        return NetworkStatus(int(status))

    def getRSSI(self):
        self._logger.debug("Get Received Signal Strength Indication (RSSI)")
        csq=self.getSingleResponse("AT+CSQ","OK","+CSQ: ")
        if csq is None: return csq
        return RSSI.fromCSQ(csq)

    def setSMSMessageFormat(self, format):
        status=self.sendATCmdWaitResp("AT+CMGF={}".format(format), "OK")
        return status==ATResp.OK

    def sendSMS(self, phoneNumber, msg):
        self._logger.debug("Send SMS: {} '{}'".format(phoneNumber, msg))
        if not self.setSMSMessageFormat(SMSMeesageFormat.Text): return False

        status=self.sendATCmdWaitResp('AT+CMGS="{}"'.format(phoneNumber), "> ")
        if status!=ATResp.OK: return False

        status,_response=self.sendATCmdWaitReturnResp(msg+"\r\n\x1a", "OK",
                timeout=11., interByteTimeout=1.2)
        return _response=="+CMGS" and status==Attempt.OK

    #### USSD
    #### PDU Mode(?)
    #### *140*# for balance(?)

if __name__=="__main__":
    s=SMS(PORT,BAUD,logging.DEBUG)
    s.setup()
    if not s.turnOn(): exit(1)
    if not s.setEchoOff(): exit(1)
    print("Good to go!")
    print(s.getIMEI())
    print(s.getSIMCCID())
    print(s.getNetworkStatus())
    print(s.getRSSI())
    print(s.sendSMS("+441234567890", "Hello World!"))