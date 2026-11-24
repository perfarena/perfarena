# Initialize previous element to head
previous = head

# Iterate while previous element is not None or empty
while previous:
    # Compare previous value to previous next value
    if previous.val == previous.next.val:
        # Point previous to the next element
        previous.next = previous.next.next
    else:
        # Move to the next element
        previous = previous.next

# Return the head of the modified linked list
return head