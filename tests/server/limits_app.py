# main.app mit kleinen Limits, damit die Server-Tests schnell laufen
import main

main.MAX_REQUESTERS = 2
main.MAX_STATS_LISTENERS = 3
main.AUTH_TIMEOUT_S = 1

app = main.app
