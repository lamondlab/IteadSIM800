from threading import Thread
from queue import Queue, Empty
from redis import Redis
import json, re, time
from datetime import datetime

from sms import SMS, BALANCE_USSD, NetworkStatus

LOGGER="SMSDispatcher"
TEN_MEGABYTES=10485760
FIVE_MINUTES=300.
PORT="/dev/ttyAMA0"
BAUD=9600

def taskWorker():
    _redis=Redis()
    _redis.set("sim800NetworkStatus", "Unknown")
    _redis.set("sim800Balance","0")
    _redis.set("sim800RSSI",0)

    logger=logging.getLogger(LOGGER)
    balanceRegExp=re.compile(r"£(\d){1,2}\.(\d){2}")

    try:
        sms=SMS(PORT,BAUD,logger=logger)
        sms.setup()
        if not sms.turnOn():
            logger.critical("Failed to turn on SMS!")
            return
        if not sms.setEchoOff():
            logger.critical("Failed to set SMS echo off!")
            return
        sms.setTime(datetime.now())

        netStat="Unknown"
        while netStat!="Good":
            netStat=sms.getNetworkStatus()
            if netStat is not None:
                if netStat in (NetworkStatus.RegisteredHome, NetworkStatus.RegisteredRoaming):
                    netStat="Good"
                elif netStat in (NetworkStatus.Searching,): netStat="Searching"
                else: netStat="Bad"
            else: netStat="Unknown"
            _redis.set("sim800NetworkStatus", netStat)

        checkBalance=True
        statusCheckTime=0.
        while True:
            if taskQueue.empty():
                if checkBalance:
                    checkBalance=False
                    balanceMsg=sms.sendUSSD(BALANCE_USSD)
                    logger.info("Balance message: {}".format(balanceMsg))
                    match=balanceRegExp.search(balanceMsg)
                    if match is not None:
                        balance=match.group(0)
                        logger.info("Balance amount: {}".format(balance))
                        _redis.set("sim800Balance",balance)

                if (time.time()-statusCheckTime)>FIVE_MINUTES:
                    rssi=sms.getRSSI()
                    if rssi is not None: rssi=(rssi.value/4.)*100
                    else: rssi=0
                    _redis.set("sim800RSSI",rssi)
                    
                    netStat=sms.getNetworkStatus()
                    if netStat is not None:
                        if netStat in (NetworkStatus.RegisteredHome, NetworkStatus.RegisteredRoaming):
                            netStat="Good"
                        elif netStat in (NetworkStatus.Searching,): netStat="Searching"
                        else: netStat="Bad"
                    else: netStat="Unknown"
                    _redis.set("sim800NetworkStatus", netStat)

                    statusCheckTime=time.time()

            try: task=taskQueue.get(timeout=60)
            except Empty: continue
            if task is None: continue

            phoneNumber=task.get('phoneNumber')
            message=task.get('message')

            if phoneNumber and message:
                logger.info("Sending SMS: {}, {}".format(phoneNumber, message))
                if sms.sendSMS(phoneNumber, message):
                    logger.info("SMS sent successfully")
                    checkBalance=True
                else: logger.error("Failed to send SMS! {}, {}".format(phoneNumber, message))
            else: logger.error("Task is not valid: {}".format(task))

            taskQueue.task_done()
    except Exception as e:
        logger.critical("Exception in task thread: {}".format(e))
        return

def main():
    logger=logging.getLogger(LOGGER)
    _redis=Redis()
    pubsub=_redis.pubsub()
    pubsub.subscribe(['sms'])
    for msg in pubsub.listen():
        if msg['channel']!=b'sms':
            logger.debug("Got message unknown channel {}".format(msg['channel']))
            continue
        if msg['type']=='subscribe':
            logger.info("Subscribed to channel")
            continue
        if msg['type']!='message':
            logger.debug("Got unknown message type {}".format(msg['type']))
            continue
        try:
            data=msg['data'].decode('utf-8')
            data=json.loads(data)
        except Exception as e:
            logging.error("Failed to decode data: {}, {}".format(msg['data'], e))
            continue
        taskQueue.put(data)

if __name__=="__main__":
    import sys, logging
    from argparse import ArgumentParser

    def exceptionHook(etype, evalue, etraceback):
        from traceback import format_tb

        logger=logging.getLogger(LOGGER)
        logstr="{name}; {value}; {traceback}".format(
            name=etype.__name__,
            value=str(evalue) or "(None)",
            traceback="\n".join(format_tb(traceback))
        )
        logger.critical(logstr)
        for h in logger.handlers:
            try: h.flush()
            except: continue

    parser=ArgumentParser(description="SMS Dispatcher.")
    parser.add_argument("-d", "--debug", dest="debug", default=False, 
        action="store_true", help="turn on debug information")
    parser.add_argument("-s", "--stdout", dest="stdout", default=False,
        action="store_true", help="re-direct logging output to stdout")
    options=parser.parse_args()
    loglevel=logging.DEBUG if options.debug else logging.WARNING    

    logger=logging.getLogger(LOGGER)
    if options.stdout: handler=logging.StreamHandler(sys.stdout)
    else:
        from logging.handlers import RotatingFileHandler
        handler=RotatingFileHandler("./smsdispatcher.log", maxBytes=TEN_MEGABYTES, backupCount=5)
    handler.setFormatter(logging.Formatter("%(asctime)s : %(levelname)s -> %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(loglevel)

    sys.excepthook=exceptionHook

    taskQueue=Queue()
    taskThread=Thread(target=taskWorker)
    taskThread.start()
    main()