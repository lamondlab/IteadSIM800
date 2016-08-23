from serial import Serial
import RPi.GPIO as IO, atexit, logging, sys
from time import sleep
from enum import IntEnum
from datetime import datetime

PORT="/dev/ttyAMA0"
BAUD=9600
GSM_ON=11
GSM_RESET=12
DATE_FMT='"%y/%m/%d,%H:%M:%S%z"'

APN="giffgaff.com"
APN_USERNAME="giffgaff"
APN_PASSWORD="" # Leave blank

BALANCE_USSD="*100#"

# Balance: *100*7#
# Remaining Credit: *100#
# Voicemail: 443 (costs 8p!)
# Text Delivery Receipt (start with): *0#
# Hide calling number: #31#

class ATResp(IntEnum):
    ErrorNoResponse=-1
    ErrorDifferentResponse=0
    OK=1

class SMSMessageFormat(IntEnum):
    PDU=0
    Text=1

class SMSTextMode(IntEnum):
    Hide=0
    Show=1

class SMSStatus(IntEnum):
    Unread=0
    Read=1
    Unsent=2
    Sent=3
    All=4

    @classmethod
    def fromStat(cls, stat):
        if stat=='"REC UNREAD"': return cls.Unread
        elif stat=='"REC READ"': return cls.Read
        elif stat=='"STO UNSENT"': return cls.Unsent
        elif stat=='"STO SENT"': return cls.Sent
        elif stat=='"ALL"': return cls.All

    @classmethod
    def toStat(cls, stat):
        if stat==cls.Unread: return "REC UNREAD"
        elif stat==cls.Read: return "REC READ"  
        elif stat==cls.Unsent: return "STO UNSENT"  
        elif stat==cls.Sent: return "STO SENT"  
        elif stat==cls.All: return "ALL"          

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
    def __init__(self, port, baud, logger=None, loglevel=logging.WARNING):
        self._port=port
        self._baud=baud

        self._ready=False
        self._serial=None

        if logger: self._logger=logger
        else:
            self._logger=logging.getLogger("SMS")
            handler=logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(asctime)s : %(levelname)s -> %(message)s"))
            self._logger.addHandler(handler)
            self._logger.setLevel(loglevel)

    def setup(self):
        """
        Setup the IO to control the power and reset inputs and the serial port.
        """
        self._logger.debug("Setup")
        IO.setmode(IO.BOARD)
        IO.setup(GSM_ON, IO.OUT, initial=IO.LOW)
        IO.setup(GSM_RESET, IO.OUT, initial=IO.LOW)
        self._serial=Serial(self._port, self._baud)

    def reset(self):
        """
        Reset (turn on) the SIM800 module by taking the power line for >1s
        and then wait 5s for the module to boot.
        """
        self._logger.debug("Reset (duration ~6.2s)")
        IO.output(GSM_ON, IO.HIGH)
        sleep(1.2)
        IO.output(GSM_ON, IO.LOW)
        sleep(5.)

    def sendATCmdWaitResp(self, cmd, response, timeout=.5, interByteTimeout=.1, attempts=1, addCR=False):
        """
        This function is designed to check for simple one line responses, e.g. 'OK'.
        """
        self._logger.debug("Send AT Command: {}".format(cmd))
        self._serial.timeout=timeout
        self._serial.inter_byte_timeout=interByteTimeout

        status=ATResp.ErrorNoResponse
        for i in range(attempts):
            bcmd=cmd.encode('utf-8')+b'\r'
            if addCR: bcmd+=b'\n'

            self._logger.debug("Attempt {}, ({})".format(i+1, bcmd))
            #self._serial.write(cmd.encode('utf-8')+b'\r')
            self._serial.write(bcmd)
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
        This function is designed to return data and check for a final response, e.g. 'OK'
        """        
        self._logger.debug("Send AT Command: {}".format(cmd))
        self._serial.timeout=timeout
        self._serial.inter_byte_timeout=interByteTimeout

        self._serial.write(cmd.encode('utf-8')+b'\r')
        self._serial.flush()
        lines=self._serial.readlines()
        for n in range(len(lines)):
            try: lines[n]=lines[n].decode('utf-8').strip()
            except UnicodeDecodeError: lines[n]=lines[n].decode('latin1').strip()

        lines=[l for l in lines if len(l) and not l.isspace()]        
        self._logger.debug("Lines: {}".format(lines))

        if not len(lines): return (ATResp.ErrorNoResponse, None)

        _response=lines.pop(-1)
        self._logger.debug("Response: {}".format(_response))
        if not len(_response) or _response.isspace(): return (ATResp.ErrorNoResponse, None)
        elif response==_response: return (ATResp.OK, lines)
        return (ATResp.ErrorDifferentResponse, None)

    def parseReply(self, data, beginning, divider=',', index=0):
        """
        Parse an AT response line by checking the reply starts with the expected prefix,
        splitting the reply into its parts by the specified divider and then return the 
        element of the response specified by index.
        """
        self._logger.debug("Parse Reply: {}, {}, {}, {}".format(data, beginning, divider, index))
        if not data.startswith(beginning): return False, None
        data=data.replace(beginning,"")
        data=data.split(divider)
        try: return True,data[index]
        except IndexError: return False, None

    def getSingleResponse(self, cmd, response, beginning, divider=",", index=0, timeout=.5, interByteTimeout=.1):
        """
        Run a command, get a single line response and the parse using the
        specified parameters.
        """
        status,data=self.sendATCmdWaitReturnResp(cmd,response,timeout=timeout,interByteTimeout=interByteTimeout)
        if status!=ATResp.OK: return None
        if len(data)!=1: return None
        ok,data=self.parseReply(data[0], beginning, divider, index)
        if not ok: return None
        return data

    def turnOn(self):
        """
        Check to see if the module is on, if so return. If not, attempt to
        reset the module and then check that it is responding.
        """
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
        """
        Switch off command echoing to simply response parsing.
        """
        self._logger.debug("Set Echo Off")
        self.sendATCmdWaitResp("ATE0", "OK")
        status=self.sendATCmdWaitResp("ATE0", "OK")
        return status==ATResp.OK

    def getLastError(self):
        """
        Get readon for last error
        """
        self._logger.debug("Get Last Error")
        error=self.getSingleResponse("AT+CEER","OK","+CEER: ")
        return error

    def getIMEI(self):
        """
        Get the IMEI number of the module
        """
        self._logger.debug("Get International Mobile Equipment Identity (IMEI)")
        status,imei=self.sendATCmdWaitReturnResp("AT+GSN","OK")
        if status==ATResp.OK and len(imei)==1: return imei[0]
        return None

    def getVersion(self):
        """
        Get the module firmware version.
        """
        self._logger.debug("Get TA Revision Identification of Software Release")
        revision=self.getSingleResponse("AT+CGMR","OK","Revision",divider=":",index=1)
        return revision

    def getSIMCCID(self):
        """
        The the SIM ICCID.
        """
        self._logger.debug("Get SIM Integrated Circuit Card Identifier (ICCID)")
        status,ccid=self.sendATCmdWaitReturnResp("AT+CCID","OK")
        if status==ATResp.OK and len(ccid)==1: return ccid[0]
        return None        

    def getNetworkStatus(self):
        """
        Get the current network connection status.
        """
        self._logger.debug("Get Network Status")
        status=self.getSingleResponse("AT+CREG?","OK","+CREG: ",index=1)
        if status is None: return status
        return NetworkStatus(int(status))

    def getRSSI(self):
        """
        Get the current signal strength in 'bars'
        """
        self._logger.debug("Get Received Signal Strength Indication (RSSI)")
        csq=self.getSingleResponse("AT+CSQ","OK","+CSQ: ")
        if csq is None: return csq
        return RSSI.fromCSQ(csq)

    def enableNetworkTimeSync(self, enable):
        self._logger.debug("Enable network time synchronisation")
        status=self.sendATCmdWaitResp("AT+CLTS={}".format(int(enable)),"OK")
        return status==ATResp.OK

    def getTime(self):
        """
        Get the current time
        """
        self._logger.debug("Get the current time")
        time=self.getSingleResponse("AT+CCLK?","OK","+CCLK: ", divider="'")
        if time is None: return time
        return datetime.strptime(time[:-1]+'00"', DATE_FMT)

    def setTime(self, time):
        """
        """
        self._logger.debug("Set the current time: {}".format(time))
        time=datetime.strftime(time, DATE_FMT)
        if time[-4]!="+": time=time[:-1]+'+00"'
        status=self.sendATCmdWaitResp("AT+CCLK={}".format(time),"OK")
        return status==ATResp.OK

    def setSMSMessageFormat(self, format):
        """
        Set the SMS message format either as PDU or text.
        """
        status=self.sendATCmdWaitResp("AT+CMGF={}".format(format), "OK")
        return status==ATResp.OK

    def setSMSTextMode(self, mode):
        status=self.sendATCmdWaitResp("AT+CSDH={}".format(mode), "OK")
        return status==ATResp.OK

    def getNumSMS(self):
        """
        Get the number of SMS on SIM card
        """
        self._logger.debug("Get Number of SMS")
        if not self.setSMSMessageFormat(SMSMessageFormat.Text):
            self._logger.error("Failed to set SMS Message Format!")
            return False

        if not self.setSMSTextMode(SMSTextMode.Show):
            self._logger.error("Failed to set SMS Text Mode!")
            return False

        num=self.getSingleResponse('AT+CPMS?', "OK", "+CPMS: ", divider='"SM",', index=1)
        if num is None: return num
        n,t,*_=num.split(',')
        return int(n),int(t)

    def readSMS(self, number):
        """
        Returns status, phone number, date/time and message in location specified by 'number'.
        """
        self._logger.debug("Read SMS: {}".format(number))
        if not self.setSMSMessageFormat(SMSMessageFormat.Text):
            self._logger.error("Failed to set SMS Message Format!")
            return None

        if not self.setSMSTextMode(SMSTextMode.Show):
            self._logger.error("Failed to set SMS Text Mode!")
            return None

        status,(params,msg)=self.sendATCmdWaitReturnResp("AT+CMGR={}".format(number),"OK")
        if status!=ATResp.OK or not params.startswith("+CMGR: "): return None

        # stat   : message status = "REC UNREAD", "REC READ", "STO UNSENT", "STO SENT", "ALL"
        # oa     : originating address
        # alpha  : string of "oa" or "da"
        # scts   : service center timestamp "YY/MM/DD,HH:MM:SS+ZZ"
        # tooa   : originating address type
        # fo     : 
        # pid    : protocol ID
        # dcs    : data coding scheme
        # sca    : 
        # tosca  : 
        # length : length of the message body
        stat,oa,alpha,scts1,scts2,tooa,fo,pid,dcs,sca,tosca,length=params[7:].split(',')

        scts=scts1+','+scts2
        tz=scts[-2:]
        scts=scts[:-1]+'00"'
        scts=datetime.strptime(scts, DATE_FMT)
        return SMSStatus.fromStat(stat),oa[1:-1],scts,msg

    def readAllSMS(self, status=SMSStatus.All):
        self._logger.debug("Read All SMS")
        if not self.setSMSMessageFormat(SMSMessageFormat.Text):
            self._logger.error("Failed to set SMS Message Format!")
            return None

        if not self.setSMSTextMode(SMSTextMode.Show):
            self._logger.error("Failed to set SMS Text Mode!")
            return None

        status,msgs=self.sendATCmdWaitReturnResp('AT+CMGL="{}"'.format(SMSStatus.toStat(status)), "OK")
        if status!=ATResp.OK or not msgs[0].startswith("+CMGL: ") or len(msgs)%2!=0: return None

        formatted=[]
        for n in range(0, len(msgs), 2):
            params,msg=msgs[n:n+2]
            if n==0: params=params[7:]
            loc,stat,oa,alpha,scts1,scts2,tooa,fo,pid,dcs,sca,tosca,length=params.split(',')
            scts=scts1+','+scts2
            tz=scts[-2:]
            scts=scts[:-1]+'00"'
            scts=datetime.strptime(scts, DATE_FMT)
            formatted.append((loc,SMSStatus.fromStat(stat),oa[1:-1],scts,msg))
        return formatted

    def deleteSMS(self, number):
        """
        Delete the SMS in location specified by 'number'.
        """
        self._logger.debug("Delete SMS: {}".format(number))
        if not self.setSMSMessageFormat(SMSMessageFormat.Text):
            self._logger.error("Failed to set SMS Message Format!")
            return False        
        status=self.sendATCmdWaitResp("AT+CMGD={:03d}".format(number), "OK")
        return status==ATResp.OK

    def sendSMS(self, phoneNumber, msg):
        """
        Send the specified message text to the provided phone number.
        """
        self._logger.debug("Send SMS: {} '{}'".format(phoneNumber, msg))
        if not self.setSMSMessageFormat(SMSMessageFormat.Text):
            self._logger.error("Failed to set SMS Message Format!")
            return False

        status=self.sendATCmdWaitResp('AT+CMGS="{}"'.format(phoneNumber), ">", addCR=True)
        if status!=ATResp.OK:
            self._logger.error("Failed to send CMGS command part 1! {}".format(status))
            return False

        cmgs=self.getSingleResponse(msg+"\r\n\x1a", "OK", "+", divider=":", timeout=11., interByteTimeout=1.2)
        return cmgs=="CMGS"

    def sendUSSD(self, ussd):
        """
        Send Unstructured Supplementary Service Data message
        """
        self._logger.debug("Send USSD: {}".format(ussd))
        reply=self.getSingleResponse('AT+CUSD=1,"{}"'.format(ussd), "OK", "+CUSD: ", index=1, timeout=11., interByteTimeout=1.2)
        return reply

if __name__=="__main__":
    s=SMS(PORT,BAUD,loglevel=logging.DEBUG)
    s.setup()
    if not s.turnOn(): exit(1)
    if not s.setEchoOff(): exit(1)
    print("Good to go!")
    print(s.getIMEI())
    print(s.getVersion())
    print(s.getSIMCCID())
    #print(s.getLastError())
    ns=s.getNetworkStatus()
    print(ns)
    if ns not in (NetworkStatus.RegisteredHome, NetworkStatus.RegisteredRoaming):
        exit(1)
    print(s.getRSSI())
    #print(s.enableNetworkTimeSync(True))
    # print(s.getTime())
    # print(s.setTime(datetime.now()))
    # print(s.getTime())
    # print(s.sendSMS("+441234567890", "Hello World!"))
    print(s.sendUSSD(BALANCE_USSD))
    #print(s.getLastError())
    print(s.getNumSMS())
    #print(s.readSMS(1))
    #print(s.deleteSMS(1))
    #print(s.readAllSMS())
