def reverse_signed_value():
    sum = 0
    sign = 1
    
    if num < 0:
        sign = -1
        num *= -1
    
    while num > 0:
        remainder = num % 10
        sum = sum * 10 + remainder
        num //= 10
    
    if sum < -2**31 or sum > 2**31 - 1:
        return 0
    else:
        return sum * sign

# Test the function with input 123
print(reverse_signed_value(123))