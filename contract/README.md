Sample responses from the ML service, checked by both sides so a change to one fails the tests of the other:

- [tests/test_contract.py](../tests/test_contract.py) checks they match `agent/api.py`'s response models exactly.
- [the backend's tests](../backend/src/test/java/com/steamtracker/backend) read them through its ML client.
