import { Boom } from '@hapi/boom'
import NodeCache from 'node-cache'
import fs from 'fs'
import readline from 'readline'
import makeWASocket, { AnyMessageContent, delay, DisconnectReason, fetchLatestBaileysVersion, getAggregateVotesInPollMessage, makeCacheableSignalKeyStore, makeInMemoryStore, PHONENUMBER_MCC, proto, useMultiFileAuthState, WAMessageContent, WAMessageKey } from '@whiskeysockets/baileys'
import express from 'express'
import P from 'pino'
import bodyParser from 'body-parser'


const startApp = async () => {
  const logger = P({ timestamp: () => `,"time":"${new Date().toJSON()}"` })
  const doReplies = !process.argv.includes('--no-reply')

  const auth_folder = "auth/baileys_auth_info"
  const store_file = "auth/baileys_store_multi.json"

  const app = express()
  app.use(bodyParser.urlencoded({ extended: false }))
  app.use(bodyParser.json())

  const port = 5000

  const msgRetryCounterCache = new NodeCache()

  const rl = readline.createInterface({ input: process.stdin, output: process.stdout })
  const question = (text: string) => new Promise<string>((resolve) => rl.question(text, resolve))

  const store = makeInMemoryStore({})
  store?.readFromFile('./auth/baileys_store_multi.json')

  setInterval(() => {
    store?.writeToFile('./auth/baileys_store_multi.json')
  }, 10_000)

  let status: { connection: string } = { connection: 'Initialising' }

  async function startSocket() {
    const { version, isLatest } = await fetchLatestBaileysVersion()
    const { state, saveCreds } = await useMultiFileAuthState('auth/baileys_auth_info')

    const socket = makeWASocket({
      version,
      printQRInTerminal: false,
      mobile: false,
      auth: state,
      msgRetryCounterCache,
      generateHighQualityLinkPreview: true,
      getMessage,
    })


    store?.bind(socket.ev)

    socket.ev.on('creds.update', saveCreds)

    socket.ev.process(
      // events is a map for event name => event data
      async (events) => {
        // something about the connection changed
        // maybe it closed, or we received all offline message or connection opened
        if (events['connection.update']) {
          const update = events['connection.update']
          const { connection, lastDisconnect } = update
          
          if (connection != undefined) {
            status.connection = connection
          }

          if (connection === 'close') {
            // reconnect if not logged out
            if ((lastDisconnect?.error as Boom)?.output?.statusCode !== DisconnectReason.loggedOut) {
              restartSocket()
            } else {
              status.connection = 'Logged out'
              console.log('Connection closed. You are logged out.')
            }
          }

          if (update.qr) {
            console.log(update.qr)
            callRC(JSON.stringify({update, action: "qr"}))
          }

          if (update.connection == "open")
            callRC(JSON.stringify({update, action: "connected"}))
          console.log('connection update', update)
        }

        // credentials updated -- save them
        if (events['creds.update']) {
          await saveCreds()
        }

        if (events['labels.association']) {
          console.log(events['labels.association'])
        }

        if (events['labels.edit']) {
          console.log(events['labels.edit'])
        }

        if (events.call) {
          console.log('recv call event', events.call)
        }

        // history received
        if (events['messaging-history.set']) {
          const { chats, contacts, messages, isLatest } = events['messaging-history.set']
          console.log(`recv ${chats.length} chats, ${contacts.length} contacts, ${messages.length} msgs (is latest: ${isLatest})`)
        }

        // received a new message
        if (events['messages.upsert']) {
          const upsert = events['messages.upsert']
          console.log('recv messages ', JSON.stringify(upsert, undefined, 2))

          if (upsert.type === 'notify') {
            for (const msg of upsert.messages) {
              if (!msg.key.fromMe && doReplies) {
                //console.log('replying to', msg.key.remoteJid)

                console.log(msg)
                callRC(JSON.stringify({msg, action: "incoming_message"}))
                //fetch("http://host.docker.internal:8000/connector/BAILEYS_EXTERNAL_TOKEN/", {method:"POST", body: JSON.stringify(msg)})
                // await socket!.readMessages([msg.key])
                //await sendMessageWTyping({ text: 'Esta é uma mensagem automática: Estou em manutenção do wpp. Desculpe o transtorno.' }, msg.key.remoteJid!)
              }
            }
          }
        }

        // messages updated like status delivered, message deleted etc.
        if (events['messages.update']) {
          console.log(
            JSON.stringify(events['messages.update'], undefined, 2)
          )

          for (const { key, update } of events['messages.update']) {
            if (update.pollUpdates) {
              const pollCreation = await getMessage(key)
              if (pollCreation) {
                console.log(
                  'got poll update, aggregation: ',
                  getAggregateVotesInPollMessage({
                    message: pollCreation,
                    pollUpdates: update.pollUpdates,
                  })
                )
              }
            }
          }
        }

        if (events['message-receipt.update']) {
          console.log(events['message-receipt.update'])
        }

        if (events['messages.reaction']) {
          console.log(events['messages.reaction'])
        }

        if (events['presence.update']) {
          console.log(events['presence.update'])
        }

        if (events['chats.update']) {
          console.log(events['chats.update'])
        }

        if (events['contacts.update']) {
          for (const contact of events['contacts.update']) {
            if (typeof contact.imgUrl !== 'undefined') {
              const newUrl = contact.imgUrl === null
                ? null
                : await socket!.profilePictureUrl(contact.id!).catch(() => null)
              console.log(
                `contact ${contact.id} has a new profile pic: ${newUrl}`,
              )
            }
          }
        }

        if (events['chats.delete']) {
          console.log('chats deleted ', events['chats.delete'])
        }
      }
    )

    async function getMessage(key: WAMessageKey): Promise<WAMessageContent | undefined> {
      if (store) {
        const msg = await store.loadMessage(key.remoteJid!, key.id!)
        return msg?.message || undefined
      }

      // only if store is present
      return proto.Message.fromObject({})
    }

    return socket
  }

  function restartSocket() {
    status.connection = 'Restarting'
    startSocket()
  }

  function callRC(body: string){
    fetch("http://host.docker.internal:8000/connector/BAILEYS_EXTERNAL_TOKEN/", {method:"POST", body})
  }

  const sock = await startSocket()

  const media: {[id:string]: Function} = {
    "image/jpeg": (url: string) => ({ image: { url } }),
    "audio/mpeg": (url: string) => ({ audio: { url }, mimetype: 'audio/mpeg'}),
    "video/mp4": (url: string) => ({ video: { url }})
  }

  app.get('/', (req, res) => {
    res.send('Hello World!')
  })

  app.get('/onWA/:phone', async (req, res) => {
    const phone_number: string = String(req.params.phone)
    const [result] = await sock.onWhatsApp(phone_number)
    res.send(result)
  })

  app.post('/send-media/:target', async (req, res) => {
    const target: string = String(req.params.target)
    const {mime, url} : {mime: string, url: string} = req.body

    console.log(mime)

    const message = media[mime](url)

    await sock.sendMessage(target, message)
    res.send("ok")
  })

  app.post('/send-message/:target', async (req, res) => {
    const target: string = String(req.params.target)
    const message: string = String(req.body.message)

    await sendMessageWTyping({ text: message}, target!)
    res.send("ok")
  })

  app.get('/status', async (req, res) => {
    res.send({
      'api': 'ok',
      'socket': status
    })
  })

  app.get('/hard-reset', async (req, res) => {
    res.sendStatus(201)

    fs.rmSync(store_file, { force: true });
    fs.rmSync(auth_folder, { recursive: true, force: true });

    restartSocket()
  })

  app.listen(port, () => {
    console.log(`Listening on port ${port}`)
  })

  async function sendMessageWTyping(msg: AnyMessageContent, jid: string) {
    await sock.presenceSubscribe(jid)
    await delay(500)

    await sock.sendPresenceUpdate('composing', jid)
    await delay(2000)

    await sock.sendPresenceUpdate('paused', jid)

    await sock.sendMessage(jid, msg)
  }
}

startApp()