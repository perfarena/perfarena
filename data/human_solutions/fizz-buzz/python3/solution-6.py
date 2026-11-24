def number_representation(n):
    output = []
    for i in range(1, n + 1):
        if i % 3 == 0 and i % 5 == 0:
            output.append("fixed bus")
        elif i % 3 == 0:
            output.append("fixed")
        elif i % 5 == 0:
            output.append("bus")
        else:
            output.append(str(i))
    return output