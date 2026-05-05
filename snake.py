#!/usr/bin/env python3
"""Snake game using the curses library."""

import curses
import random
import time

# Directions
UP    = (-1, 0)
DOWN  = ( 1, 0)
LEFT  = ( 0,-1)
RIGHT = ( 0, 1)

KEY_MAP = {
    curses.KEY_UP:    UP,
    curses.KEY_DOWN:  DOWN,
    curses.KEY_LEFT:  LEFT,
    curses.KEY_RIGHT: RIGHT,
    ord('w'): UP,
    ord('s'): DOWN,
    ord('a'): LEFT,
    ord('d'): RIGHT,
}

OPPOSITES = {UP: DOWN, DOWN: UP, LEFT: RIGHT, RIGHT: LEFT}


def draw_border(win, h, w):
    win.attron(curses.color_pair(3))
    win.border()
    win.attroff(curses.color_pair(3))


def place_food(snake, h, w):
    while True:
        pos = (random.randint(1, h - 2), random.randint(1, w - 2))
        if pos not in snake:
            return pos


def draw_hud(win, score, high_score, w):
    hud = f" Score: {score}  Best: {high_score} "
    win.attron(curses.color_pair(4))
    win.addstr(0, max(0, (w - len(hud)) // 2), hud)
    win.attroff(curses.color_pair(4))


def game_over_screen(win, score, high_score, h, w):
    messages = [
        "  GAME OVER  ",
        f"  Score: {score}  ",
        f"  Best:  {high_score}  ",
        "",
        "  [R] Restart  [Q] Quit  ",
    ]
    box_h = len(messages) + 2
    box_w = max(len(m) for m in messages) + 4
    y = (h - box_h) // 2
    x = (w - box_w) // 2

    win.attron(curses.color_pair(5))
    for i, line in enumerate(messages):
        win.addstr(y + 1 + i, x + 2, line.center(box_w - 4))
    win.attroff(curses.color_pair(5))
    win.refresh()

    while True:
        key = win.getch()
        if key in (ord('r'), ord('R')):
            return True
        if key in (ord('q'), ord('Q'), 27):
            return False


def play(win, high_score):
    curses.curs_set(0)
    win.nodelay(True)
    win.keypad(True)
    h, w = win.getmaxyx()

    # Initial snake in the middle, length 3
    mid_r, mid_c = h // 2, w // 2
    snake = [(mid_r, mid_c + i) for i in range(2, -1, -1)]
    direction = RIGHT
    food = place_food(snake, h, w)
    score = 0
    speed = 0.15  # seconds per tick

    while True:
        # Input
        key = win.getch()
        new_dir = KEY_MAP.get(key, direction)
        if new_dir != OPPOSITES.get(direction):
            direction = new_dir

        # Move
        head = (snake[0][0] + direction[0], snake[0][1] + direction[1])

        # Collision: wall or self
        if (head[0] <= 0 or head[0] >= h - 1 or
                head[1] <= 0 or head[1] >= w - 1 or
                head in snake):
            high_score = max(score, high_score)
            return score, high_score

        snake.insert(0, head)

        if head == food:
            score += 10
            high_score = max(score, high_score)
            food = place_food(snake, h, w)
            speed = max(0.05, speed - 0.002)  # speed up
        else:
            tail = snake.pop()
            win.addch(tail[0], tail[1], ' ')

        # Draw
        win.erase()
        draw_border(win, h, w)
        draw_hud(win, score, high_score, w)

        # Food
        win.attron(curses.color_pair(2))
        win.addch(food[0], food[1], '●')
        win.attroff(curses.color_pair(2))

        # Snake body
        win.attron(curses.color_pair(1))
        for i, seg in enumerate(snake):
            ch = '█' if i == 0 else '▓'
            try:
                win.addch(seg[0], seg[1], ch)
            except curses.error:
                pass
        win.attroff(curses.color_pair(1))

        win.refresh()
        time.sleep(speed)


def main(stdscr):
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN,  -1)  # snake
    curses.init_pair(2, curses.COLOR_RED,    -1)  # food
    curses.init_pair(3, curses.COLOR_CYAN,   -1)  # border
    curses.init_pair(4, curses.COLOR_YELLOW, -1)  # HUD
    curses.init_pair(5, curses.COLOR_WHITE,  curses.COLOR_RED)  # game over

    high_score = 0

    while True:
        stdscr.clear()
        stdscr.refresh()
        score, high_score = play(stdscr, high_score)
        if not game_over_screen(stdscr, score, high_score, *stdscr.getmaxyx()):
            break


if __name__ == '__main__':
    curses.wrapper(main)
