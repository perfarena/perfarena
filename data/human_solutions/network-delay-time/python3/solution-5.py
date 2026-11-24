def findSmallestLetter(letters, Target):
    retVal = letters[-1]  # Initialize to greatest possible character
    for C in letters:
        if C > Target:
            retVal = min(retVal, C)
    return retVal if retVal != letters[-1] else letters[0]