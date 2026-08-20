import pyautogui
import time
print("Waiting 3 seconds, focus a text field...")
time.sleep(3)
pyautogui.hotkey('win', 'h')
print("Triggered Win+H")
