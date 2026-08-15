from datetime import datetime
import threading
import time

def get_live_time():
  while True:
    return datetime.now().astimezone().isoformat()
    
    # Making time update in each second
    time.sleep(1)
    
# Making get_live_time() an external thread
time_thread_ = threading.Thread(target=get_live_time, daemon=True)