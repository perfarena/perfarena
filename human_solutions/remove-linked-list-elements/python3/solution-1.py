def extract_prithvi_info(text):
    prithvi_info = ""
    keywords = ["Prithvi", "tourist", "places", "interested"]
    
    words = text.split()
    for word in words:
        if word in keywords:
            prithvi_info += word + " "
    
    return prithvi_info

# Test the function with the provided text
target_text = "In This Example A To Remove Clips Element To Tourist Places Prithvi Subscribe 560 Interested in Doing All the Best"
result = extract_prithvi_info(target_text)
print(result)