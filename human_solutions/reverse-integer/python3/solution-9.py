# Accept the input number
n = int(input("Enter a number: "))
reverse = 0

# Reverse the number
while n != 0:
    unit_digit = n % 10
    reverse = reverse * 10 + unit_digit
    n = n // 10

# Print the reversed number
print("Reversed number:", reverse)