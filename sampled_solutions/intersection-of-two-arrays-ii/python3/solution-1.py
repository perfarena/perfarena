def findUniqueElements(foodItemsSet):
    uniqueElementsSet = set()
    foodItemsList = list(foodItemsSet)
    
    for item in foodItemsList:
        if item not in uniqueElementsSet:
            uniqueElementsSet.add(item)
    
    return uniqueElementsSet

# Example usage
foodItems = {'apple', 'banana', 'apple', 'orange', 'banana'}
uniqueItems = findUniqueElements(foodItems)
print(uniqueItems)