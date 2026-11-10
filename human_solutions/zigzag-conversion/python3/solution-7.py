def convert_to_zigzag(s, numRows):
    if numRows == 1:
        return s

    rows = ['' for _ in range(numRows)]
    current_row, direction = 0, 1

    for char in s:
        rows[current_row] += char
        if current_row == 0:
            direction = 1
        elif current_row == numRows - 1:
            direction = -1
        current_row += direction

    return ''.join(rows)