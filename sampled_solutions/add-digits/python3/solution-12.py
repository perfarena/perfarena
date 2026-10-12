def add_digits(num):
    num_str = str(num)
    
    while len(num_str) > 1:
        new_num = 0
        for digit in num_str:
            new_num += int(digit)
        
        num_str = str(new_num)
    
    return int(num_str)

# Test the function
num = 258
result = add_digits(num)
print(result)  # Output should be 6