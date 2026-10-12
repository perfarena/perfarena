# Define the set of odd digits
odds = set([1, 3, 5, 7, 9])

# Iterate through the string from right to left
for i in range(len(num) - 1, -1, -1):
    if int(num[i]) in odds:
        return num[:i+1]

# Return an empty string if no odd digit is found
return ""