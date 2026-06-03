# import requests
# import json
#
#
# headers = {
#     "accept": "text/html, application/json",
#     "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
#     "content-type": "application/json",
#     "device-memory": "32",
#     "downlink": "3.3",
#     "dpr": "1.5",
#     "ect": "4g",
#     "origin": "https://www.amazon.com",
#     "priority": "u=1, i",
#     "referer": "https://www.amazon.com/mh?ref_=nb_sb_ss_di_ci_mcx_mi_ci-mcx-ksf-of-nv1_1&sf=F1&s=B0F6MTPQVG&crid=2ZBGFISRKLM2V",
#     "rtt": "250",
#     "sec-ch-device-memory": "32",
#     "sec-ch-dpr": "1.5",
#     "sec-ch-ua": "\"Chromium\";v=\"148\", \"Microsoft Edge\";v=\"148\", \"Not/A)Brand\";v=\"99\"",
#     "sec-ch-ua-full-version-list": "\"Chromium\";v=\"148.0.7778.168\", \"Microsoft Edge\";v=\"148.0.3967.70\", \"Not/A)Brand\";v=\"99.0.0.0\"",
#     "sec-ch-ua-mobile": "?0",
#     "sec-ch-ua-platform": "\"Windows\"",
#     "sec-ch-ua-platform-version": "\"19.0.0\"",
#     "sec-ch-viewport-height": "782",
#     "sec-ch-viewport-width": "2552",
#     "sec-fetch-dest": "empty",
#     "sec-fetch-mode": "cors",
#     "sec-fetch-site": "same-origin",
#     "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0",
#     "viewport-width": "2552",
#     "x-amz-acp-params": "tok=4khwMOffdjfnofxI6EWkhePkpvTa_QHqSwt-B9lqqY0;ts=1779862470109;rid=F6B7AX2DVY9WXBW2TMCA;d1=230;d2=AN;tpm=CGHDB.content-id;ref=pd_ci_mcx_mh_pe_rm_d1",
#     "x-amz-amabot-click-attributes": "disable",
#     "x-amzn-flow-closure-id": "1779862156",
#     "x-requested-with": "XMLHttpRequest"
# }
# cookies = {
#     "session-id": "138-5391316-1548230",
#     "ubid-main": "130-8078460-7790727",
#     "skin": "noskin",
#     "av-profile": "cGlkPWFtem4xLmFjdG9yLnBlcnNvbi5vaWQuQTJBQ0ZCRFUyVFFVQUEmdGltZXN0YW1wPTE3Nzg1NTc5NjM5MDkmdmVyc2lvbj12MQ.gymLLtOvpYNCM-oOxqkgXAGdyoJ5FG-o26cqgfe1hTPbAAAAAQAAAABqAqQLcmF3AAAAAPgWC9WfHH8iB-olH_E9xQ",
#     "av-timezone": "Asia/Shanghai",
#     "x-amz-captcha-1": "1778844421606858",
#     "x-amz-captcha-2": "QIGsR4AwMEB9rGTn9mODsg==",
#     "ss-currency-check-dismiss": "1",
#     "sso-state-main": "Xdsso|ZQHMlvBalQKITrrwuPMBNR6J_Zw8LFUH7zTKjlVHFEG4NvyFYwYUthcnXnYpYcO4h_-8mlFrufXkkxTsjU9sPXq6UEyHHN_covdz3bEqcVrbuomz",
#     "at-main": "Atza|gQAeOQM7AwEBALPnmrm9ALzinW6-ZiGJ4I-iEAvV1s3SYotYudyl6D3HUyd3XrR98Q0z8hLBb_qBDyE3nEd9xAdVzcjWYcd_U9iRBguFIhPkj3EgKjJ7w0K_VzV0rF_zRLoOKfVx6XQBQTSmuHFE2Ns2KhSOysR7NyjeF8aKk4QXmcNzTBO6lI_LxSTU2nAuh2mnljEilkHeWUfSzTcW8XbiqCz_tS5_TX1JQWkF66Ryji4qRMDiyNHX5KT3iaj3e7X9ZnPUclbOQDdDhVzEIsSqcY1lbQeu_iJmNSUAIKziGIpyboZwNbSdEayZN5UYFJFVCZiYTZflMODEgcMj6oljH4aneQeub_k",
#     "sess-at-main": "CM0ZiClSKSYClswnuOQbAT47evyNJ4kQPcvNA/HQzvA=",
#     "sst-main": "Sst1|PQIysqNUnv5zs-5ukPpe3oyRDUJDlFTnyVO2o5jQAs1DVVlEPzb-SvrJzgnsA5IQoxNs3sKUZZRsP8pVuADnbOFFN5dlj0I6fEJJEnot9Oqa9BTGAvsJCv_-CELhll0aNKKm4IXO2T2hTGgdUCF_6zNQDrvEkLZUbrzDKFYvySpplzPPpiyH-G2zhcRT5-qG6JtVvCrh6ghc7tkw2nhMIs9uUyiMKkuVbTMI3vmThYxcgJJYchCsVsAgrtCq1wrEAtqYmOr-6aUcbPphfzcRbCkNDzSmfuBJJXFngw75HXpg9vnlRbfw8wl7Xr-P9Yh190ahX9_FJn5C0CcC-HgDo_2AUVdIDudrfglfOT6qLeIpCSMiKAnm2Dn_2HezjXwJ2oZO",
#     "session-id-time": "2082787201l",
#     "i18n-prefs": "USD",
#     "lc-main": "en_US",
#     "sp-cdn": "J4F7",
#     "b2b": "\"VFJVRQ==\"",
#     "session-token": "Q+V/0BUtr2LoyJutbcABBDUHZ4IrfbNvvJcvTcLcQYFD0M4ltwJhHc3LS2wxXPShtwYV4dJLk/e9Gy13KHEpgWNjT2seu7sotTnzCMz+b40bCes+9Ig2oprdfyl7X6FE4JliM2Md2sRlqMvjwtjQ8ICqFx6KC3DZuwRu0wsrK4KvYVV5CRrmWFKQWbA8w7uv0YB2qMcKESlaYk0+Y7pPBptFnFUyFmAVigTwT/xn/uDFbMfFCK59zPS96hhTmvZL",
#     "x-main": "\"wybQ@sCK0TuN2jAgTObs0j?1Tal4oF4YMAhE7cRR2RaMtirk?ZlASQvXB2nm?lzc\"",
#     "cmc": "IQk2bWSO2YZKLArUifE+wgmqxE0rB1nH724NEzoJ8pULbx7sZMxAHPkKbYKQPH8yVAOSFiFkv42a1zk0qBl/8mgMjTJj5rRkhfdbIW6435R3K2J98HgNPVDCPWSgy1Ct/Gv6lQzAcpI/PTm8MiNpAgKGQK206aKz6B1dPFPatblxe5lRztgBWws2Z39U8ZyrFkNqeVFSZAyaYzqxMvSSgGsxM7wpIuFbLnP+v5BX1yq71ZEhoxyLyhTA7GFWxK0=",
#     "rxc": "AL8ZEMBeCTZtFfON3DU",
#     "csm-hit": "tb:s-F6B7AX2DVY9WXBW2TMCA|1779862471024&t:1779862471523&adb:adblk_no",
#     "rx": "AQChGhKZfZXdGX/YWXAALYg4wZo=@AvxfFmo="
# }
# url = "https://www.amazon.com/acp/p13n-intuition-desktop/p13n-intuition-desktop-086236a6-62c2-439c-8b89-d4df395026cd-1779818475856/getIntuitionWidgets"
# params = {
#     "page-type": "P13NMobileMission",
#     "pd_rd_w": "mYPQe",
#     "content-id": "amzn1.sym.ace7bda0-f0aa-4605-94dd-7a08f0cf397d",
#     "pf_rd_p": "ace7bda0-f0aa-4605-94dd-7a08f0cf397d",
#     "pf_rd_r": "F6B7AX2DVY9WXBW2TMCA",
#     "pd_rd_wg": "rF5Qm",
#     "pd_rd_r": "8dbb03a7-5571-4674-90d1-dd12374bd15f",
#     "ref_": "pd_ci_mcx_mh_pe_rm_d1",
#     "stamp": "1779862470356"
# }
# data = {
#     "locale": "en_US",
#     "pageAsin": "B0F6MTPQVG",
#     "p13n-product-explorer": "{\"version\":\"1\",\"attributeList\":[],\"attributeType\":{},\"contextAsin\":\"B0F6MTPQVG\",\"contextAsinSource\":\"0\",\"missionInfo\":\"\",\"amazonElementPillList\":[{\"value\":\"allPrime\",\"typeId\":\"prime\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"allPrime\"]},{\"value\":\"freeOneDay\",\"typeId\":\"prime\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"freeOneDay\"]},{\"value\":\"freeSameDay\",\"typeId\":\"prime\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"freeSameDay\"]},{\"value\":\"freeOvernight\",\"typeId\":\"prime\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"freeOvernight\"]},{\"value\":\"fourStarsAndAbove\",\"typeId\":\"rating\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"fourStarsAndAbove\"]},{\"value\":\"threeStarsAndAbove\",\"typeId\":\"rating\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"threeStarsAndAbove\"]},{\"value\":\"twoStarsAndAbove\",\"typeId\":\"rating\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"twoStarsAndAbove\"]},{\"value\":\"oneStarAndAbove\",\"typeId\":\"rating\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"oneStarAndAbove\"]},{\"value\":\"priceTier1\",\"typeId\":\"price\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"priceTier1\"]},{\"value\":\"priceTier2\",\"typeId\":\"price\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"priceTier2\"]},{\"value\":\"priceTier3\",\"typeId\":\"price\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"priceTier3\"]},{\"value\":\"priceTier4\",\"typeId\":\"price\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"priceTier4\"]},{\"value\":\"allDeals\",\"typeId\":\"deals\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"allDeals\"]}],\"requestType\":\"FIRST_LOAD_EXPANSION\"}",
#     "slateToken": "AgAAS0YwRAIgAyyoL6ITs6ZiKkosgHtkFLBtVUq0ntfdzZ7r0Hj51iwCICwuaPeO7iYEOjmiZAQI58YiSeyZpSPAEcbbYJX64LNvAnYxAAEATQGeaBH84STpIwJ2MSY2t3lst5fD1uSNNpGq9sMCuHSFa4wwgFA35Z0/UAAIAAAACAZyZXRhaWwKYW1hem9uLmNvbQVlbi1VU1VTRAGqAgANCgIAZQsBAg8EahaKjAMAAAQAAA==",
#     "landingView": "mhfy",
#     "platform": "DESKTOP",
#     "placement": "MH",
#     "slot": "FY",
#     "strategyId": "MissionCX-Product-Explorer-D-NonEng-T1"
# }
# data = json.dumps(data, separators=(',', ':'))
# response = requests.post(url, headers=headers, cookies=cookies, params=params, data=data)
#
# print(response.text)
# print(response)




import requests
import json


import time

timestamp_ms = int(time.time() * 1000)    # 13位整型
print(timestamp_ms)  # 输出类似 1779818475856


headers = {
    "accept": "text/html, application/json",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "content-type": "application/json",
    "device-memory": "32",
    "downlink": "2.25",
    "dpr": "1.5",
    "ect": "4g",
    "origin": "https://www.amazon.com",
    "priority": "u=1, i",
    "referer": "https://www.amazon.com/mh?ref_=nb_sb_ss_di_ci_mcx_mi_ci-mcx-ksf-of-nv1_3&sf=F1&s=B0FWJ8HNCB&crid=2OEJ6TTAXOVCV",
    "rtt": "300",
    "sec-ch-device-memory": "32",
    "sec-ch-dpr": "1.5",
    "sec-ch-ua": "\"Chromium\";v=\"148\", \"Microsoft Edge\";v=\"148\", \"Not/A)Brand\";v=\"99\"",
    "sec-ch-ua-full-version-list": "\"Chromium\";v=\"148.0.7778.168\", \"Microsoft Edge\";v=\"148.0.3967.70\", \"Not/A)Brand\";v=\"99.0.0.0\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "sec-ch-ua-platform-version": "\"19.0.0\"",
    "sec-ch-viewport-height": "943",
    "sec-ch-viewport-width": "2552",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0",
    "viewport-width": "2552",
    "x-amz-acp-params": "tok=uO6rs2WR83XmNHokZzH2jDKCjZDEFwYjLfjEYQzHYiA;ts=1779863270109;rid=MXBYRQBF34Z2Y2NPEXEW;d1=230;d2=AN;tpm=CGHDB.content-id;ref=pd_ci_mcx_mh_pe_rm_d1",
    "x-amz-amabot-click-attributes": "disable",
    "x-amzn-flow-closure-id": "1779863047",
    "x-requested-with": "XMLHttpRequest"
}
cookies = {
    "session-id": "138-5391316-1548230",
    "ubid-main": "130-8078460-7790727",
    "skin": "noskin",
    "av-profile": "cGlkPWFtem4xLmFjdG9yLnBlcnNvbi5vaWQuQTJBQ0ZCRFUyVFFVQUEmdGltZXN0YW1wPTE3Nzg1NTc5NjM5MDkmdmVyc2lvbj12MQ.gymLLtOvpYNCM-oOxqkgXAGdyoJ5FG-o26cqgfe1hTPbAAAAAQAAAABqAqQLcmF3AAAAAPgWC9WfHH8iB-olH_E9xQ",
    "av-timezone": "Asia/Shanghai",
    "x-amz-captcha-1": "1778844421606858",
    "x-amz-captcha-2": "QIGsR4AwMEB9rGTn9mODsg==",
    "ss-currency-check-dismiss": "1",
    "sso-state-main": "Xdsso|ZQHMlvBalQKITrrwuPMBNR6J_Zw8LFUH7zTKjlVHFEG4NvyFYwYUthcnXnYpYcO4h_-8mlFrufXkkxTsjU9sPXq6UEyHHN_covdz3bEqcVrbuomz",
    "at-main": "Atza|gQAeOQM7AwEBALPnmrm9ALzinW6-ZiGJ4I-iEAvV1s3SYotYudyl6D3HUyd3XrR98Q0z8hLBb_qBDyE3nEd9xAdVzcjWYcd_U9iRBguFIhPkj3EgKjJ7w0K_VzV0rF_zRLoOKfVx6XQBQTSmuHFE2Ns2KhSOysR7NyjeF8aKk4QXmcNzTBO6lI_LxSTU2nAuh2mnljEilkHeWUfSzTcW8XbiqCz_tS5_TX1JQWkF66Ryji4qRMDiyNHX5KT3iaj3e7X9ZnPUclbOQDdDhVzEIsSqcY1lbQeu_iJmNSUAIKziGIpyboZwNbSdEayZN5UYFJFVCZiYTZflMODEgcMj6oljH4aneQeub_k",
    "sess-at-main": "CM0ZiClSKSYClswnuOQbAT47evyNJ4kQPcvNA/HQzvA=",
    "sst-main": "Sst1|PQIysqNUnv5zs-5ukPpe3oyRDUJDlFTnyVO2o5jQAs1DVVlEPzb-SvrJzgnsA5IQoxNs3sKUZZRsP8pVuADnbOFFN5dlj0I6fEJJEnot9Oqa9BTGAvsJCv_-CELhll0aNKKm4IXO2T2hTGgdUCF_6zNQDrvEkLZUbrzDKFYvySpplzPPpiyH-G2zhcRT5-qG6JtVvCrh6ghc7tkw2nhMIs9uUyiMKkuVbTMI3vmThYxcgJJYchCsVsAgrtCq1wrEAtqYmOr-6aUcbPphfzcRbCkNDzSmfuBJJXFngw75HXpg9vnlRbfw8wl7Xr-P9Yh190ahX9_FJn5C0CcC-HgDo_2AUVdIDudrfglfOT6qLeIpCSMiKAnm2Dn_2HezjXwJ2oZO",
    "session-id-time": "2082787201l",
    "i18n-prefs": "USD",
    "lc-main": "en_US",
    "sp-cdn": "J4F7",
    "b2b": "\"VFJVRQ==\"",
    "rx": "AQC0HRKZfZXdGX/YWXAALYg4wZo=@AvxfFmo=",
    "ak_bmsc": "2B1C168D841C5560FBE3B21A2EFC8881~000000000000000000000000000000~YAAQXElDFzUVDkqeAQAAqnMYaB/97mJ0c6iMlnUsq3ZNjyVGe69+oNq4HVCa2O4Fb89cU8/b2exur6J3+dVcVj6mWcBQp7V3OMdMwe1XRK7gw6o6s5TSWATDEW4/p6GUS+ecr3XqQ91dEff4CdHYu/Qm70/NMYGa0TLhHhOuy7Dfp3e9tnVL2Lwn82HPxCV8klBksLSQESvpSeaE7dxwA6I0+678NfwzTYc77Afzs544FyeoUzByfwHyDRsJBsno9wOWu3V9OxgH92D7FV5nYuwDeeJ9bHY+N1ZjUe/zqRfV9Ug4G5asTprZC/gVsTNiSf9CjeEZykn9Attq+Q4eNstpRPqen7jCC1IKqp5Jp63Q/2Czw0hl9reyf/RxaQMV0HhG+g==",
    "bm_sv": "CD2FAE64A36173CCA26E04783F4F11B1~YAAQXElDF60VDkqeAQAAQZMYaB/r7mXj8ANLnCQHV3MIfJ3S0s1D0nnD7hvjggfsgZGiSxxEP9C+WfehufjmFHPtrzRdSpabuhE77HuOxSmzMghNGm6CJIiAzAw2953zxoGVKPb3T7ceosQ3ErEDV0Qod0qhXCsy0UhlJUMT46JasgOW1aFaZIZ9sjMn2EVrQSIr9ghugkvuE527reIffQJANiZFr+k3N7vSvR/g6vYWXCy7rKdnRZT+20GtmSu6~1",
    "session-token": "uGQhvKq0/7FfvWKFLPR2omMCyq7Ua0lBGWE+r7KQAMQXgFplM2V2lhk++QjB9p9QFQcLbcXZuHiFFMEFOsc63aEXahhF31J0RAEH8G7kC2KT4Tz0MTw1GdJG3Yc0sR53b39BLIX2thN4F7CLSBIN0o9c8ng2c2L1+hRE5VoEeNdvQ4DGdZh4PmDCYSHxjNGsozvcePNeBU7cFI8UVvuQTebcbNBmJW9EyF74vnnk83btsl7lz7hCD4nhEWeQ13em",
    "x-main": "Lch8T1b9Lsg0obhzsIQyNaywvZag9dE7aSZpzSAa6HD4sml3AvuYNvNiQV9xgzWJ",
    "rxc": "AL8ZEMB8DDZtFfONcTA",
    "cmc": "fgw2bWSOy+pLSBu59v5a0WCzxEQqDEvcnB8NEysJ9YQLbxLsecIQSL0cK4LNKyVjRUTAB2M1to2Cl3Z6/wg4rXhC2jxp8+E7krkcMmSn3ZoufTV9rXFXal7caSWpmwS8uzvwlwzVZd8wOSXwbTInQgTFVOjqvrP5qkETYEDZrf4pJZIe3c9YVwgsKm9G8Z2hQgIzdwsHdVfMcmfwN63BjGY2PL5kdfAIeCDwosAdnHrmwc538A3LikiQsDVFxqq8lGi/ydIr96uj4M0v3XS9iYuEf4wCpusnlwyzhg==",
    "csm-hit": "tb:s-MXBYRQBF34Z2Y2NPEXEW|1779863271184&t:1779863271370&adb:adblk_no"
}
url = f"https://www.amazon.com/acp/p13n-intuition-desktop/p13n-intuition-desktop-086236a6-62c2-439c-8b89-d4df395026cd-1779818475856/getIntuitionWidgets"
params = {
    "page-type": "P13NMobileMission",
    "pd_rd_w": "j5hn0",
    "content-id": "amzn1.sym.ace7bda0-f0aa-4605-94dd-7a08f0cf397d",
    "pf_rd_p": "ace7bda0-f0aa-4605-94dd-7a08f0cf397d",
    "pf_rd_r": "MXBYRQBF34Z2Y2NPEXEW",
    "pd_rd_wg": "vjpjP",
    "pd_rd_r": "c834f775-37f3-4f12-926a-0702d6d647a8",
    "ref_": "pd_ci_mcx_mh_pe_rm_d1",
    "stamp": "1779863270423"
}
data = {
    "locale": "en_US",
    "pageAsin": "B0FWJ8HNCB",
    "p13n-product-explorer": "{\"version\":\"1\",\"attributeList\":[],\"attributeType\":{},\"contextAsin\":\"B0FWJ8HNCB\",\"contextAsinSource\":\"0\",\"missionInfo\":\"\",\"amazonElementPillList\":[{\"value\":\"allPrime\",\"typeId\":\"prime\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"allPrime\"]},{\"value\":\"freeOneDay\",\"typeId\":\"prime\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"freeOneDay\"]},{\"value\":\"freeSameDay\",\"typeId\":\"prime\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"freeSameDay\"]},{\"value\":\"freeOvernight\",\"typeId\":\"prime\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"freeOvernight\"]},{\"value\":\"fourStarsAndAbove\",\"typeId\":\"rating\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"fourStarsAndAbove\"]},{\"value\":\"threeStarsAndAbove\",\"typeId\":\"rating\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"threeStarsAndAbove\"]},{\"value\":\"twoStarsAndAbove\",\"typeId\":\"rating\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"twoStarsAndAbove\"]},{\"value\":\"oneStarAndAbove\",\"typeId\":\"rating\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"oneStarAndAbove\"]},{\"value\":\"priceTier1\",\"typeId\":\"price\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"priceTier1\"]},{\"value\":\"priceTier2\",\"typeId\":\"price\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"priceTier2\"]},{\"value\":\"priceTier3\",\"typeId\":\"price\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"priceTier3\"]},{\"value\":\"priceTier4\",\"typeId\":\"price\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"priceTier4\"]},{\"value\":\"allDeals\",\"typeId\":\"deals\",\"selectable\":true,\"selected\":false,\"display\":\"\",\"rawValues\":[\"allDeals\"]}],\"requestType\":\"FIRST_LOAD_EXPANSION\"}",
    "slateToken": "AgAATEcwRQIgcUjyrkVPztT6UuSxJeQ4pl230b8W5+s4G8YHw87pJHICIQCbADAtH7KC0aCfP4K4yT22HjH3pfObqh6e2uFvzkHSewJ2MQABAE0BnmgeMdsyWCMCdjHa0QQlCOyHRXHmEbKs+ZDhtHm5nRTg0ME3X2KuFxkiPAAAAAgGcmV0YWlsCmFtYXpvbi5jb20FZW4tVVNVU0QBqgIADQoCAGULAQIPBGoWjgcDAAAEAAA=",
    "landingView": "mhfy",
    "platform": "DESKTOP",
    "placement": "MH",
    "slot": "FY",
    "strategyId": "MissionCX-Product-Explorer-D-NonEng-T1"
}
data = json.dumps(data, separators=(',', ':'))
response = requests.post(url, headers=headers, cookies=cookies, params=params, data=data)

print(response.text)
print(response)