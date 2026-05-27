import tkinter as tk
import random
import time
from threading import Thread
import os
from PIL import ImageGrab
from datetime import datetime

class SimonSaysGame:
    def __init__(self, root):
        self.root = root
        self.root.title("Simon Says")
        self.root.geometry("1920x1080")
        self.root.configure(bg="#1a1a1a")
        self.root.resizable(True, True)
        
        # Create screenshots folder
        self.screenshots_root = "screenshots"
        if not os.path.exists(self.screenshots_root):
            os.makedirs(self.screenshots_root)
            print(f"Created '{self.screenshots_root}' folder for screenshots")

        # State file the runner polls instead of pixel-scanning the window
        self.state_file = os.path.join(self.screenshots_root, "game_state.txt")
        self._write_state("idle")

        self.current_game_folder = None
        
        # Game variables
        self.colors = ["red", "blue", "yellow", "green"]
        self.sequence = []
        self.player_sequence = []
        self.level = 1
        self.game_active = False
        self.can_click = False
        
        # Button references for visual feedback
        self.buttons = {}
        self.button_refs = {}
        
        # Create UI elements
        self.create_widgets()
        
    def create_widgets(self):
        """Create all UI elements"""
        
        # Title
        title_label = tk.Label(
            self.root,
            text="SIMON SAYS",
            font=("Arial", 36, "bold"),
            bg="#1a1a1a",
            fg="#00ff00"
        )
        title_label.pack(pady=20)
        
        # Game info frame
        info_frame = tk.Frame(self.root, bg="#1a1a1a")
        info_frame.pack(pady=10)
        
        self.level_label = tk.Label(
            info_frame,
            text=f"Level: {self.level}",
            font=("Arial", 16, "bold"),
            bg="#1a1a1a",
            fg="#00ff00"
        )
        self.level_label.pack(side=tk.LEFT, padx=20)
        
        self.status_label = tk.Label(
            info_frame,
            text="Press Start to Begin",
            font=("Arial", 14),
            bg="#1a1a1a",
            fg="#ffff00"
        )
        self.status_label.pack(side=tk.LEFT, padx=20)
        
        # Simon buttons frame
        buttons_frame = tk.Frame(self.root, bg="#1a1a1a")
        buttons_frame.pack(pady=20)
        
        # Create 2x2 grid of colored buttons
        positions = [
            (0, 0, "red"),
            (0, 1, "blue"),
            (1, 0, "yellow"),
            (1, 1, "green")
        ]
        
        # Use Canvas instead of Button so the black-circle highlight is a
        # drawn item, not a text glyph. Canvas size is pinned in pixels and
        # never changes when we add/remove the circle, so layout stays stable.
        self.BTN_W, self.BTN_H = 120, 100
        for row, col, color in positions:
            canvas = tk.Canvas(
                buttons_frame,
                width=self.BTN_W,
                height=self.BTN_H,
                bg=color,
                highlightthickness=2,
                highlightbackground="#333333",
                bd=0,
            )
            canvas.grid(row=row, column=col, padx=10, pady=10)
            canvas.bind("<Button-1>", lambda e, c=color: self.player_click(c))
            self.button_refs[color] = canvas
        
        # Control buttons frame
        control_frame = tk.Frame(self.root, bg="#1a1a1a")
        control_frame.pack(pady=20)
        
        
        self.start_button = tk.Button(
            control_frame,
            text="START GAME",
            font=("Arial", 14, "bold"),
            bg="#00ff00",
            fg="black",
            command=self.start_game,
            width=15,
            height=2
        )
        self.start_button.pack(side=tk.LEFT, padx=10)
        
        self.reset_button = tk.Button(
            control_frame,
            text="RESET",
            font=("Arial", 14, "bold"),
            bg="#ff0000",
            fg="white",
            command=self.reset_game,
            width=15,
            height=2
        )
        self.reset_button.pack(side=tk.LEFT, padx=10)
        
        # Sequence display frame
        display_frame = tk.Frame(self.root, bg="#1a1a1a")
        display_frame.pack(pady=10)
        
        tk.Label(
            display_frame,
            text="Sequence:",
            font=("Arial", 12, "bold"),
            bg="#1a1a1a",
            fg="#00ff00"
        ).pack()
        
        self.sequence_label = tk.Label(
            display_frame,
            text="",
            font=("Arial", 11),
            bg="#1a1a1a",
            fg="#00ffff"
        )
        self.sequence_label.pack()
        
        self.update_sequence_display()
    
    def _write_state(self, state: str):
        """Write the current game state to a file the AI runner polls.

        States used:
            idle       — game has never started (or was reset)
            watching   — Simon is currently flashing the new color
            your_turn  — player should click; sequence has been shown
            game_over  — player got it wrong; press START to restart
        """
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                f.write(state)
        except Exception as e:
            print(f"Could not write state file {self.state_file}: {e}")

    def start_game(self):
        """Start a new game"""
        if self.game_active:
            return

        # Create a new folder for this game session
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_game_folder = self.screenshots_root
        os.makedirs(self.current_game_folder, exist_ok=True)
        print(f"\nStarting new game session: {self.current_game_folder}")

        self.game_active = True
        self.level = 1
        self.sequence = []
        self.player_sequence = []
        self.can_click = False
        self.start_button.config(state=tk.DISABLED)

        self.play_game_round()
    
    def play_game_round(self):
        """Play a round of the game"""
        # Add new color to sequence
        self.sequence.append(random.choice(self.colors))
        self.player_sequence = []

        # Update level display
        self.level_label.config(text=f"Level: {self.level}")
        self.status_label.config(text="Watch the sequence...", fg="#ffff00")
        self._write_state("watching")
        self.update_sequence_display()

        # Play the sequence
        self.root.after(500, self.show_sequence)
    
    def show_sequence(self):
        """Show only the NEW (last) color in the sequence and screenshot it.

        Earlier colors already have permanent level_NN_sequence.png screenshots
        from previous rounds, so the AI player has the full sequence by reading
        all level_*.png files in order. Replaying the whole sequence every
        round adds 0.8s per color (~10s+ at high levels) for no information
        gain — and causes the runner's wait loop to time out.
        """
        def play_sequence():
            if self.sequence:
                last_color = self.sequence[-1]
                # Brief flash for visual feedback (and the human's benefit)
                self.highlight_button(last_color)
                self.play_sound(last_color)
                time.sleep(0.3)

                # Static circle for the screenshot
                circle_id = self._draw_circle(last_color)
                time.sleep(0.3)
                self.take_screenshot()
                self._erase_circle(last_color, circle_id)

            # Allow player to click
            self.can_click = True
            self.status_label.config(text="Your turn!", fg="#00ff00")
            self._write_state("your_turn")

        thread = Thread(target=play_sequence, daemon=True)
        thread.start()

    def _draw_circle(self, color):
        """Draw a black filled circle on the colored button's Canvas.

        Returns the Canvas item id so the caller can erase it later.
        Canvas dimensions are fixed in pixels, so adding/removing the
        circle never changes the layout.
        """
        canvas = self.button_refs[color]
        w, h = self.BTN_W, self.BTN_H
        pad = int(min(w, h) * 0.2)
        circle_id = canvas.create_oval(
            pad, pad, w - pad, h - pad,
            fill="black", outline="",
        )
        canvas.update()
        return circle_id

    def _erase_circle(self, color, circle_id):
        canvas = self.button_refs[color]
        canvas.delete(circle_id)
        canvas.update()

    def highlight_button(self, color):
        """Flash a black filled circle on the button for ~300 ms."""
        circle_id = self._draw_circle(color)
        time.sleep(0.3)
        self._erase_circle(color, circle_id)
    
    def take_screenshot(self):
        """Take a screenshot of the current sequence"""
        try:
            if not self.current_game_folder:
                return
            
            # Get the window coordinates
            x = self.root.winfo_rootx()
            y = self.root.winfo_rooty()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            
            # Take screenshot of the window
            screenshot = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            
            # Save screenshot with level number in the game session folder
            filename = os.path.join(self.current_game_folder, f"level_{self.level:02d}_sequence.png")
            screenshot.save(filename)
            print(f"Screenshot saved: {filename}")
        except Exception as e:
            print(f"Error taking screenshot: {e}")
    
    def play_sound(self, color):
        """Sound disabled"""
        pass
    
    def player_click(self, color):
        """Handle player button click"""
        if not self.can_click or not self.game_active:
            return
        
        self.highlight_button(color)
        self.play_sound(color)
        
        self.player_sequence.append(color)
        
        # Check if player's input is correct
        if self.player_sequence[-1] != self.sequence[len(self.player_sequence) - 1]:
            # Wrong color
            self.game_over()
            return
        
        # Check if player completed the sequence
        if len(self.player_sequence) == len(self.sequence):
            self.can_click = False
            self.player_sequence = []
            self.level += 1
            self.status_label.config(text="Correct! Next level...", fg="#00ff00")
            self.root.after(1500, self.play_game_round)
    
    def game_over(self):
        """Handle game over"""
        self.game_active = False
        self.can_click = False
        self.start_button.config(state=tk.NORMAL)

        self.status_label.config(text=f"Game Over! Final Level: {self.level}", fg="#ff0000")

        # Write failure details for the AI runner to pick up. Written BEFORE
        # the state transition so the runner sees it as soon as it reads
        # state=game_over.
        try:
            failure_path = os.path.join(self.screenshots_root, "last_failure.txt")
            with open(failure_path, "w", encoding="utf-8") as f:
                f.write(f"level={self.level}\n")
                f.write(f"intended={','.join(self.sequence)}\n")
                f.write(f"player={','.join(self.player_sequence)}\n")
        except Exception as e:
            print(f"Could not write failure file: {e}")

        self._write_state("game_over")

        print(f"\n=== GAME OVER ===")
        print(f"Final Level: {self.level}")
        print(f"Sequence Length: {len(self.sequence)}")
        print(f"====================\n")

    def reset_game(self):
        """Reset the game"""
        self.game_active = False
        self.can_click = False
        self.level = 1
        self.sequence = []
        self.player_sequence = []
        self.start_button.config(state=tk.NORMAL)

        self.level_label.config(text=f"Level: {self.level}")
        self.status_label.config(text="Press Start to Begin", fg="#ffff00")
        self._write_state("idle")
        self.update_sequence_display()
    
    def update_sequence_display(self):
        """Update the sequence display label"""
        if self.sequence:
            color_symbols = {
                "red": "🔴",
                "blue": "🔵",
                "yellow": "🟡",
                "green": "🟢"
            }
            display = " ".join([color_symbols.get(c, c) for c in self.sequence])
            self.sequence_label.config(text=display)
        else:
            self.sequence_label.config(text="")


def main():
    root = tk.Tk()
    game = SimonSaysGame(root)
    root.mainloop()


if __name__ == "__main__":
    main()
