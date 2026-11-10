def zigzag_conversion(s, num_rows):
    if num_rows == 1 or num_rows >= len(s):
        return s

    rows = ['' for _ in range(num_rows)]
    level = 0
    direction = 1

    for char in s:
        rows[level] += char
        if level == 0:
            direction = 1
        elif level == num_rows - 1:
            direction = -1
        level += direction

    return ''.join(rows)

# Example usage
result = zigzag_conversion("PAYPALISHIRING", 3)
print(result)  # Output: "PAHNAPLSIIGYIR"