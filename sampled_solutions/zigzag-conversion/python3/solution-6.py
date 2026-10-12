def convert_to_zigzag(s, numRows):
    if numRows == 1:
        return s

    result = ""
    n = len(s)
    cycle_len = numRows * 2 - 2

    for i in range(numRows):
        j = 0
        while j + i < n:
            result += s[j + i]
            if i != 0 and i != numRows - 1:
                additional_index = j + cycle_len - i
                if additional_index < n:
                    result += s[additional_index]
            j += cycle_len

    return result

# Example usage
input_string = "PAYPALISHIRING"
num_rows = 3
result = convert_to_zigzag(input_string, num_rows)
print(result)