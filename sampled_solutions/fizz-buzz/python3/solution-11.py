# Initialize resultant array
result = []

# Iterate from 1 to n
for i in range(1, n+1):
    if i % 3 == 0 and i % 5 == 0:
        result.append("Fus")
    elif i % 3 == 0:
        result.append("F")
    elif i % 5 == 0:
        result.append("bus")
    else:
        result.append(str(i))

# Return the final result
return result