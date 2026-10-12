def zigzag_conversion(s, num_rows):
    if num_rows == 1 or num_rows >= len(s):
        return s

    rows = ['' for _ in range(num_rows)]
    row = 0
    down = True

    for char in s:
        rows[row] += char
        if row == 0:
            down = True
        elif row == num_rows - 1:
            down = False

        row = row + 1 if down else row - 1

    return ''.join(rows)

# Test cases
print(zigzag_conversion("PAYPALISHIRING", 3))  # Output: "PAHNAPLSIIGYIR"
print(zigzag_conversion("PAYPALISHIRING", 4))  # Output: "PINALSIGYAHRPI"