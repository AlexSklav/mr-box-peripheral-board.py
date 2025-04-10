import dropbot
try:
    board1 = dropbot.SerialProxy()
    print("DropBot instance created")
except Exception as e:
    print(f"Error with dropbot: {e}")

import mr_box_peripheral_board
try:
    board2 = mr_box_peripheral_board.SerialProxy()
    print("Mr Box instance created")
except Exception as e:
    print(f"Error with mr_box: {e}")
