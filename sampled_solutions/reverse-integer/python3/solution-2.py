def reverse_integer(x):
    answer = 0
    INT_MAX = 2**31 - 1
    INT_MIN = -2**31
    
    while x != 0:
        digit = x % 10
        x = x // 10
        
        if answer > INT_MAX // 10 or (answer == INT_MAX // 10 and digit > 7):
            return 0
        if answer < INT_MIN // 10 or (answer == INT_MIN // 10 and digit < -8):
            return 0
        
        answer = answer * 10 + digit
    
    return answer

# Test the function
input_num = 123
output = reverse_integer(input_num)
print(output)  # Output should be 321